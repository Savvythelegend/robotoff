import datetime
import functools
import itertools
import operator

import elasticsearch
import numpy as np
from cachetools.func import ttl_cache
from elasticsearch.helpers import bulk as elasticsearch_bulk
from elasticsearch.helpers import scan as elasticsearch_scan
from more_itertools import chunked

from robotoff import settings
from robotoff.elasticsearch import get_es_client
from robotoff.insights.annotate import UPDATED_ANNOTATION_RESULT, annotate
from robotoff.insights.importer import import_insights
from robotoff.models import (
    ImagePrediction,
    LogoAnnotation,
    LogoConfidenceThreshold,
    LogoEmbedding,
)
from robotoff.models import Prediction as PredictionModel
from robotoff.models import ProductInsight, db
from robotoff.off import OFFAuthentication
from robotoff.types import (
    ElasticSearchIndex,
    InsightImportResult,
    JSONType,
    LogoLabelType,
    Prediction,
    PredictionType,
    ServerType,
)
from robotoff.utils import get_logger
from robotoff.utils.cache import function_cache_register
from robotoff.utils.text import get_tag

logger = get_logger(__name__)


LOGO_TYPE_MAPPING: dict[str, PredictionType] = {
    "brand": PredictionType.brand,
    "label": PredictionType.label,
}

UNKNOWN_LABEL: LogoLabelType = ("UNKNOWN", None)


BoundingBoxType = tuple[float, float, float, float]


def load_resources():
    """Load and cache resources."""
    get_logo_confidence_thresholds()
    get_logo_annotations()


def compute_iou(box_1: BoundingBoxType, box_2: BoundingBoxType) -> float:
    """Compute the IoU (intersection over union) for two bounding boxes.

    The boxes are expected to have the following format:
    (y_min, x_min, y_max, x_max).
    """
    y_min_1, x_min_1, y_max_1, x_max_1 = box_1
    y_min_2, x_min_2, y_max_2, x_max_2 = box_2
    x_max = min(x_max_1, x_max_2)
    x_min = max(x_min_1, x_min_2)
    y_max = min(y_max_1, y_max_2)
    y_min = max(y_min_1, y_min_2)
    width_inter = max(0, x_max - x_min)
    height_inter = max(0, y_max - y_min)
    area_inter = width_inter * height_inter
    box_1_area = (x_max_1 - x_min_1) * (y_max_1 - y_min_1)
    box_2_area = (x_max_2 - x_min_2) * (y_max_2 - y_min_2)
    union_area = box_1_area + box_2_area - area_inter
    return area_inter / union_area


def filter_logos(
    logos: list[JSONType], score_threshold: float, iou_threshold: float = 0.95
) -> list[tuple[int, JSONType]]:
    """Select logos that don't intersect with each other
    (IoU < `iou_threshold`) and that have a confidence score above
    `score_threshold`.

    Return a list of (original_idx, logo) tuples.
    """
    filtered = []
    skip_indexes = set()
    for i in range(len(logos)):
        logo = logos[i]
        if i not in skip_indexes:
            for j in range(i + 1, len(logos)):
                if (
                    compute_iou(logo["bounding_box"], logos[j]["bounding_box"])
                    >= iou_threshold
                ):
                    # logos are sorted by descending confidence score, so we
                    # ignore j logo (logo with lower confidence score)
                    skip_indexes.add(j)

        if logo["score"] >= score_threshold:
            filtered.append((i, logo))

    return filtered


@functools.cache
def get_logo_confidence_thresholds() -> dict[LogoLabelType, float]:
    logger.debug("Loading logo confidence thresholds from DB...")
    thresholds = {}

    for item in LogoConfidenceThreshold.select().iterator():
        thresholds[(item.type, item.value)] = item.threshold

    return thresholds


def get_stored_logo_ids(es_client: elasticsearch.Elasticsearch) -> set[int]:
    scan_iter = elasticsearch_scan(
        es_client,
        query={"query": {"match_all": {}}},
        index=ElasticSearchIndex.logo,
        source=False,
    )
    return set(int(item["_id"]) for item in scan_iter)


def delete_ann_logos(
    es_client: elasticsearch.Elasticsearch, logo_ids: list[int]
) -> int:
    """Delete logos from the ANN index.

    :param es_client: Elasticsearch client
    :param logo_ids: a list of logo ids to delete
    :return: the number of logos deleted
    """
    actions = (
        {
            "_op_type": "delete",
            "_index": ElasticSearchIndex.logo.name,
            "_id": logo_id,
        }
        for logo_id in logo_ids
    )
    success, _ = elasticsearch_bulk(es_client, actions)
    return success


def add_logos_to_ann(
    es_client: elasticsearch.Elasticsearch,
    logo_embeddings: list[LogoEmbedding],
    server_type: ServerType,
) -> None:
    """Index logo embeddings in Elasticsearch ANN index.

    :param es_client: Elasticsearch client
    :param logo_embeddings: a list of `LogoEmbedding`s model instances, the
        fields `logo_id` and `embedding` should be available
    :param server_type: the server type (project) associated with the logo
        embeddings
    """
    embeddings = [
        np.frombuffer(logo_embedding.embedding, dtype=np.float32)
        for logo_embedding in logo_embeddings
    ]
    actions = (
        {
            "_index": ElasticSearchIndex.logo.name,
            "_id": logo_embedding.logo_id,
            "embedding": embedding / np.linalg.norm(embedding),
            "server_type": server_type.name,
        }
        for logo_embedding, embedding in zip(logo_embeddings, embeddings)
    )
    elasticsearch_bulk(es_client, actions)


def save_nearest_neighbors(
    es_client: elasticsearch.Elasticsearch,
    logo_embeddings: list[LogoEmbedding],
    server_type: ServerType,
) -> None:
    """Save nearest neighbors of a batch of logo embedding."""
    updated = []
    for logo_embedding in logo_embeddings:
        results = knn_search(
            es_client,
            logo_embedding.embedding,
            settings.K_NEAREST_NEIGHBORS,
            server_type,
        )
        results = [item for item in results if item[0] != logo_embedding.logo_id][
            : settings.K_NEAREST_NEIGHBORS
        ]

        if results:
            logo_ids, distances = zip(*results)
            logo_embedding.logo.nearest_neighbors = {
                "distances": distances,
                "logo_ids": logo_ids,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            updated.append(logo_embedding.logo)

    if updated:
        LogoAnnotation.bulk_update(updated, fields=["nearest_neighbors"], batch_size=50)


def knn_search(
    client: elasticsearch.Elasticsearch,
    embedding_bytes: bytes,
    k: int = settings.K_NEAREST_NEIGHBORS,
    server_type: ServerType | None = None,
) -> list[tuple[int, float]]:
    """Search for k approximate nearest neighbors of `embedding_bytes` in the
    Elasticsearch logos index.

    :param client: Elasticsearch client
    :param embedding_bytes: 1d array of the logo embedding serialized using
        `numpy.tobytes()`
    :param k: number of nearest neighbors to return, defaults to
        `settings.K_NEAREST_NEIGHBORS`
    :param server_type: the server type (project) associated with the logos
        to be returned. If not provided, logos from all projects are returned.
    """
    embedding = np.frombuffer(embedding_bytes, dtype=np.float32)
    knn_body = {
        "field": "embedding",
        "query_vector": embedding / np.linalg.norm(embedding),
        "k": k + 1,
        "num_candidates": k + 1,
    }

    if server_type is not None:
        knn_body["filter"] = {"term": {"server_type": server_type.name}}

    results = client.search(
        index=ElasticSearchIndex.logo, knn=knn_body, source=False, size=k + 1
    )
    if hits := results["hits"]["hits"]:
        return [(int(hit["_id"]), 1.0 - hit["_score"]) for hit in hits]

    return []


# ttl: 1h
@ttl_cache(maxsize=1, ttl=3600)
def get_logo_annotations() -> dict[int, LogoLabelType]:
    logger.debug("Loading logo annotations from DB...")
    annotations: dict[int, LogoLabelType] = {}

    for logo in (
        LogoAnnotation.select(
            LogoAnnotation.id,
            LogoAnnotation.annotation_type,
            LogoAnnotation.annotation_value,
            LogoAnnotation.taxonomy_value,
        )
        .where(LogoAnnotation.annotation_type.is_null(False))
        .iterator()
    ):
        if logo.annotation_value is None:
            annotations[logo.id] = (logo.annotation_type, None)
        elif logo.taxonomy_value is not None:
            annotations[logo.id] = (logo.annotation_type, logo.taxonomy_value)

    return annotations


def predict_label(logo: LogoAnnotation) -> LogoLabelType | None:
    probs = predict_proba(logo)

    if probs is None or not probs:
        return None

    return sorted(probs.items(), key=operator.itemgetter(0))[0][0]


def predict_proba(
    logo: LogoAnnotation, weights: str = "distance"
) -> dict[LogoLabelType, float] | None:
    if logo.nearest_neighbors is None:
        return None

    nn_distances = logo.nearest_neighbors["distances"]
    nn_logo_ids = logo.nearest_neighbors["logo_ids"]

    logo_annotations = get_logo_annotations()

    nn_labels: list[LogoLabelType] = []
    for nn_logo_id in nn_logo_ids:
        nn_labels.append(logo_annotations.get(nn_logo_id, UNKNOWN_LABEL))

    return _predict_proba(nn_logo_ids, nn_labels, nn_distances, weights)


def _predict_proba(
    logo_ids: list[int],
    nn_labels: list[LogoLabelType],
    nn_distances: list[float],
    weights: str,
) -> dict[LogoLabelType, float]:
    weights = get_weights(np.array(nn_distances), weights)
    labels: list[LogoLabelType] = [UNKNOWN_LABEL] + [
        x for x in set(nn_labels) if x != UNKNOWN_LABEL
    ]
    label_to_id = {label: i for i, label in enumerate(labels)}
    proba_k = np.zeros(len(labels))
    pred_labels = np.array([label_to_id[x] for x in nn_labels])

    for i, idx in enumerate(pred_labels.T):
        proba_k[idx] += weights[i]

    proba_k /= proba_k.sum()

    prediction: dict[LogoLabelType, float] = {}
    for i in range(len(proba_k)):
        prediction[labels[i]] = float(proba_k[i])

    return prediction


def get_weights(dist: np.ndarray, weights: str = "uniform"):
    """Get the weights from an array of distances and a parameter ``weights``
    Parameters
    ----------
    dist : ndarray
        The input distances
    weights : {'uniform', 'distance' or a callable}
        The kind of weighting used
    Returns
    -------
    weights_arr : array of the same shape as ``dist``
    """
    if weights in (None, "uniform"):
        return np.ones_like(dist)

    elif weights == "distance":
        # if user attempts to classify a point that was zero distance from one
        # or more training points, those training points are weighted as 1.0
        # and the other points as 0.0
        with np.errstate(divide="ignore"):
            dist = 1.0 / dist
        inf_mask = np.isinf(dist)
        inf_row = np.any(inf_mask)
        dist[inf_row] = inf_mask[inf_row]
        return dist
    elif callable(weights):
        return weights(dist)
    else:
        raise ValueError(
            "weights not recognized: should be 'uniform', "
            "'distance', or a callable function"
        )


def import_logo_insights(
    logos: list[LogoAnnotation],
    thresholds: dict[LogoLabelType, float],
    server_type: ServerType,
    default_threshold: float = 0.2,
) -> InsightImportResult:
    """Generate and import insights from logos.

    Unannotated logos are considered instances of label "UNKNOWN". The
    majority class is used as prediction, and the "UNKNOWN" prediction is
    ignored.

    :param logos: a list of `LogoAnnotation` model instances, used to generate
        insights
    :param thresholds: a dict of confidence thresholds for each class. If the
        class is not defined, default threshold is used
    :param server_type: the server type (project) associated with the logos
    :param default_threshold: the default confidence threshold to use,
        defaults to 0.2
    :return: the result from the insight import
    """
    selected_logos = []
    logo_probs = []
    for logo in logos:
        probs = predict_proba(logo)

        if not probs:
            continue

        label, max_prob = max(
            ((label, prob) for label, prob in probs.items() if label != UNKNOWN_LABEL),
            default=(UNKNOWN_LABEL, 0.0),
            key=operator.itemgetter(1),
        )
        threshold = thresholds[label] if label in thresholds else default_threshold

        if label == UNKNOWN_LABEL or max_prob < threshold:
            continue

        selected_logos.append(logo)
        logo_probs.append(probs)

    if not logos:
        return InsightImportResult()

    # Delete all predictions for these logos from universal logo detectors
    # that are not from a human annotator
    PredictionModel.delete().where(
        (
            PredictionModel.data["logo_id"]
            .cast("integer")
            .in_([logo.id for logo in logos])
        )
        & (~(PredictionModel.data["is_annotation"].cast("bool") == True))  # noqa: E712
        # Add a filter on barcode to speed-up filtering
        & (PredictionModel.barcode.in_([logo.barcode for logo in logos]))
    ).execute()
    predictions = predict_logo_predictions(selected_logos, logo_probs, server_type)
    import_result = import_insights(predictions, server_type)

    return import_result


def generate_insights_from_annotated_logos_job(
    logo_ids: list[int], auth: OFFAuthentication, server_type: ServerType
):
    """Wrap generate_insights_from_annotated_logos function into a python-rq
    compatible job."""
    with db:
        logos = list(
            LogoAnnotation.select(LogoAnnotation, ImagePrediction)
            .where(LogoAnnotation.id.in_(logo_ids))
            .join(ImagePrediction)
        )

        if logos:
            generate_insights_from_annotated_logos(logos, auth, server_type)


def generate_insights_from_annotated_logos(
    logos: list[LogoAnnotation], auth: OFFAuthentication, server_type: ServerType
) -> int:
    """Generate and apply insights from annotated logos.

    :param logos: a list of `LogoAnnotation` model instances, used to generate
        insights
    :param auth: the authentication credentials to use for the annotation
    :param server_type: the server type (project) associated with the logos
    :return: the number of insights annotated
    """
    predictions = []
    for logo in logos:
        logger.debug("Generating prediction for logo %s", logo.id)
        prediction = generate_prediction(
            logo_type=logo.annotation_type,
            logo_value=logo.taxonomy_value,
            automatic_processing=False,  # we're going to apply it immediately
            data={
                "logo_id": logo.id,
                "bounding_box": logo.bounding_box,
                "username": logo.username,
                "is_annotation": True,  # it's worth restating it
            },
            confidence=1.0,
            server_type=server_type,
            model_version=logo.image_prediction.model_version,
        )

        if prediction is None:
            logger.debug("No prediction generated for logo %s", logo.id)
            continue

        prediction.barcode = logo.barcode
        prediction.source_image = logo.source_image
        logger.debug("Prediction generated for logo %s: %s", logo.id, prediction)
        predictions.append(prediction)

    import_result = import_insights(predictions, server_type)
    if import_result.created_predictions_count():
        logger.info(import_result)
    else:
        logger.debug("No insight created")

    annotated = 0
    for created_id in itertools.chain.from_iterable(
        insight_import_result.insight_created_ids
        for insight_import_result in import_result.product_insight_import_results
    ):
        insight: ProductInsight | None = ProductInsight.get_or_none(id=created_id)
        if insight:
            logger.info(
                "Annotating insight %s (%s)",
                insight.id,
                insight.get_product_id(),
            )
            annotation_result = annotate(insight, 1, auth=auth)
            annotated += int(annotation_result == UPDATED_ANNOTATION_RESULT)

    return annotated


def predict_logo_predictions(
    logos: list[LogoAnnotation],
    logo_probs: list[dict[LogoLabelType, float]],
    server_type: ServerType,
) -> list[Prediction]:
    predictions = []

    for logo, probs in zip(logos, logo_probs):
        if not probs:
            continue

        label, max_prob = max(
            ((label, prob) for label, prob in probs.items() if label != UNKNOWN_LABEL),
            default=(UNKNOWN_LABEL, 0.0),
            key=operator.itemgetter(1),
        )

        if label == UNKNOWN_LABEL:
            continue

        prediction = generate_prediction(
            logo_type=label[0],
            logo_value=label[1],
            confidence=max_prob,
            data={
                "logo_id": logo.id,
                "bounding_box": logo.bounding_box,
            },
            server_type=server_type,
            model_version=logo.image_prediction.model_version,
        )

        if prediction is not None:
            prediction.barcode = logo.barcode
            prediction.source_image = logo.source_image
            predictions.append(prediction)

    return predictions


def generate_prediction(
    logo_type: str,
    logo_value: str | None,
    data: dict,
    confidence: float,
    server_type: ServerType,
    automatic_processing: bool | None = False,
    model_version: str | None = None,
) -> Prediction | None:
    """Generate a Prediction from a logo.

    The Prediction may either be created after the annotation of the logo by
    a human (in which case the insight should be annotated right after
    creation), or by infering the logo value from nearest neighbor labels.

    Currently, only brand and label logo types are supported: None is returned
    if the logo type is different, or if the logo_value is None.
    """
    if logo_type not in LOGO_TYPE_MAPPING or logo_value is None:
        return None

    prediction_type = LOGO_TYPE_MAPPING[logo_type]

    value_tag = None
    value = None

    if prediction_type == PredictionType.brand:
        value = logo_value
        value_tag = get_tag(value)

    elif prediction_type == PredictionType.label:
        value_tag = logo_value

    return Prediction(
        type=prediction_type,
        value_tag=value_tag,
        value=value,
        automatic_processing=automatic_processing,
        predictor="universal-logo-detector",
        predictor_version=model_version,
        data=data,
        confidence=confidence,
        server_type=server_type,
    )


def refresh_nearest_neighbors(
    server_type: ServerType, day_offset: int = 7, batch_size: int = 500
):
    """Refresh each logo nearest neighbors if the last refresh is more than
    `day_offset` days old."""
    sql_query = """
        SELECT
        id
        FROM
        logo_annotation
        WHERE
        (
            logo_annotation.completed_at IS NULL
            AND (
                logo_annotation.nearest_neighbors IS NULL
                OR ((logo_annotation.nearest_neighbors ->> 'updated_at') ::timestamp < (now() - '%s days' ::interval))
            )
        );"""
    logo_ids = [item[0] for item in db.execute_sql(sql_query, (day_offset,))]
    logger.info("%s logos to refresh", len(logo_ids))

    es_client = get_es_client()
    thresholds = get_logo_confidence_thresholds()

    for logo_id_batch in chunked(logo_ids, batch_size):
        with db.atomic():
            logo_embeddings = list(
                LogoEmbedding.select(LogoEmbedding, LogoAnnotation)
                .join(LogoAnnotation)
                .where(LogoEmbedding.logo_id.in_(logo_id_batch))
            )
            try:
                save_nearest_neighbors(es_client, logo_embeddings, server_type)
            except (
                elasticsearch.ConnectionError,
                elasticsearch.ConnectionTimeout,
            ) as e:
                logger.info("Request error during ANN batch query", exc_info=e)
            else:
                logos = [embedding.logo for embedding in logo_embeddings]
                import_logo_insights(
                    logos, thresholds=thresholds, server_type=server_type
                )

    logger.info("refresh of logo nearest neighbors finished")


function_cache_register.register(get_logo_confidence_thresholds)
function_cache_register.register(get_logo_annotations)
