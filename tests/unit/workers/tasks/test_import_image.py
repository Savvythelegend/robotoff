import pytest
from openfoodfacts.types import TaxonomyType

from robotoff.prediction.ingredient_list import (
    IngredientPredictionAggregatedEntity,
    IngredientPredictionOutput,
)
from robotoff.prediction.langid import LanguagePrediction
from robotoff.taxonomy import get_taxonomy
from robotoff.workers.tasks.import_image import (
    add_ingredient_in_taxonomy_field,
    convert_legacy_ingredient_image_prediction_data,
    generate_ingredient_prediction_data,
    get_text_from_bounding_box,
)

from ...pytest_utils import get_ocr_result_asset


@pytest.mark.parametrize(
    "ocr_asset_path, bounding_box, image_width, image_height, expected_text",
    [
        (
            "/robotoff/tests/unit/ocr/5400910301160_1.json",
            (
                0.2808293402194977,
                0.37121888995170593,
                0.35544055700302124,
                0.49409016966819763,
            ),
            882,
            1200,
            "NUTRIDIA ",
        ),
        (
            "/robotoff/tests/unit/ocr/9421023629015_5.json",
            (0.342327416, 0.469950765, 0.512927711, 0.659323752),
            901,
            1200,
            "manuka health\nNEW ZEALAND\n",
        ),
    ],
)
def test_get_text_from_bounding_box(
    ocr_asset_path: str,
    bounding_box: tuple[int, int, int, int],
    expected_text: str,
    image_width: int,
    image_height: int,
):
    ocr_result = get_ocr_result_asset(ocr_asset_path)
    assert ocr_result is not None
    text = get_text_from_bounding_box(
        ocr_result, bounding_box, image_width, image_height
    )
    assert text == expected_text


def test_add_ingredient_in_taxonomy_field():
    parsed_ingredients = [
        {
            "id": "en:water",
            "text": "water",
            "percent_min": 33.3333333333333,
            "percent_max": 100,
            "percent_estimate": 66.6666666666667,
            "vegan": "yes",
            "vegetarian": "yes",
        },
        {
            "id": "en:salt",
            "text": "salt",
            "percent_min": 0,
            "percent_max": 50,
            "percent_estimate": 16.6666666666667,
            "vegan": "yes",
            "vegetarian": "yes",
        },
        {
            "id": "en:sugar",
            "text": "sugar",
            "percent_min": 0,
            "percent_max": 33.3333333333333,
            "percent_estimate": 16.6666666666667,
            "vegan": "yes",
            "vegetarian": "yes",
            "ingredients": [
                {
                    "id": "en:glucose",
                    "text": "glucose",
                    "percent_min": 0,
                    "percent_max": 100,
                    "percent_estimate": 100,
                    "vegan": "yes",
                    "vegetarian": "yes",
                },
                {
                    "id": "en:unknown-ingredient",
                    "text": "Unknown ingredient",
                    "percent_min": 0,
                    "percent_max": 100,
                    "percent_estimate": 100,
                },
            ],
        },
    ]
    ingredient_taxonomy = get_taxonomy(TaxonomyType.ingredient, offline=True)

    total_ingredients_n, known_ingredients_n = add_ingredient_in_taxonomy_field(
        parsed_ingredients, ingredient_taxonomy
    )

    assert total_ingredients_n == 5
    assert known_ingredients_n == 4

    assert parsed_ingredients == [
        {
            "id": "en:water",
            "text": "water",
            "percent_min": 33.3333333333333,
            "percent_max": 100,
            "percent_estimate": 66.6666666666667,
            "vegan": "yes",
            "vegetarian": "yes",
            "in_taxonomy": True,
        },
        {
            "id": "en:salt",
            "text": "salt",
            "percent_min": 0,
            "percent_max": 50,
            "percent_estimate": 16.6666666666667,
            "vegan": "yes",
            "vegetarian": "yes",
            "in_taxonomy": True,
        },
        {
            "id": "en:sugar",
            "text": "sugar",
            "percent_min": 0,
            "percent_max": 33.3333333333333,
            "percent_estimate": 16.6666666666667,
            "vegan": "yes",
            "vegetarian": "yes",
            "in_taxonomy": True,
            "ingredients": [
                {
                    "id": "en:glucose",
                    "text": "glucose",
                    "percent_min": 0,
                    "percent_max": 100,
                    "percent_estimate": 100,
                    "vegan": "yes",
                    "vegetarian": "yes",
                    "in_taxonomy": True,
                },
                {
                    "id": "en:unknown-ingredient",
                    "text": "Unknown ingredient",
                    "percent_min": 0,
                    "percent_max": 100,
                    "percent_estimate": 100,
                    "in_taxonomy": False,
                },
            ],
        },
    ]


def test_generate_ingredient_prediction_data_invalid_language_code(mocker):
    entities = [
        IngredientPredictionAggregatedEntity(
            start=0,
            end=10,
            raw_end=10,
            score=0.9,
            text="water, salt",
            lang=LanguagePrediction(lang="en", confidence=0.9),  # Valid 2-letter code
            bounding_box=(0, 0, 100, 100),
        ),
        IngredientPredictionAggregatedEntity(
            start=15,
            end=25,
            raw_end=25,
            score=0.8,
            text="sucre, farine",
            lang=LanguagePrediction(
                # 3-letter code, we shouldn't send it to parse_ingredients
                # as it is not a valid 2-letter language code
                lang="sah",
                confidence=0.8,
            ),
            bounding_box=(0, 0, 100, 100),
        ),
        IngredientPredictionAggregatedEntity(
            start=30,
            end=40,
            raw_end=40,
            score=0.7,
            text="agua, sal",
            lang=LanguagePrediction(lang="es", confidence=0.7),  # Valid 2-letter code
            bounding_box=(0, 0, 100, 100),
        ),
    ]

    ingredient_prediction_output = IngredientPredictionOutput(
        entities=entities, text="water, salt. sucre, farine. agua, sal."
    )

    mock_parse = mocker.patch(
        "robotoff.workers.tasks.import_image.parse_ingredients",
        return_value=[
            {
                "id": "en:water",
                "text": "water",
                "in_taxonomy": True,
            }
        ],
    )
    result = generate_ingredient_prediction_data(
        ingredient_prediction_output, image_width=800, image_height=600
    )

    assert len(result["entities"]) == 3

    # Check that the entity with valid language codes (en, es) were processed
    # (should have ingredients_n field)
    valid_entities = [
        entity for entity in result["entities"] if "ingredients_n" in entity
    ]
    # Only entities with valid 2-letter language codes should be processed
    assert len(valid_entities) == 2

    # Check that the entity with invalid language code (sah) was not processed
    # (should not have ingredients_n field)
    invalid_entities = [
        entity
        for entity in result["entities"]
        if "ingredients_n" not in entity and entity["lang"]["lang"] == "sah"
    ]
    assert len(invalid_entities) == 1

    # Verify parse_ingredients was only called for valid language codes
    assert mock_parse.call_count == 2  # Called for "en" and "es", but not "sah"

    # Verify that the function was called with the correct language codes
    call_args = [
        call[0][1] for call in mock_parse.call_args_list
    ]  # Extract lang_id from calls
    assert "en" in call_args
    assert "es" in call_args
    assert "sah" not in call_args


def test_convert_legacy_missing_ingredients_n():
    image_prediction_data = {
        "entities": [
            {
                # Entity with ingredients_n field
                "end": 328,
                "lang": {"lang": "it", "confidence": 0.9},
                "text": "Riso integrale cotto",
                "score": 0.99,
                "start": 121,
                "raw_end": 328,
                "ingredients_n": 4,
                "known_ingredients_n": 3,
                "unknown_ingredients_n": 1,
                "bounding_box": [76, 120, 611, 185],
            },
            {
                # Entity without ingredients_n field
                "end": 671,
                "lang": {"lang": "ro", "confidence": 1.0},
                "text": "pe",
                "score": 0.5,
                "start": 669,
                "raw_end": 671,
                "bounding_box": [975, 5, 996, 12],
            },
        ]
    }

    # This should not raise a KeyError
    result = convert_legacy_ingredient_image_prediction_data(
        image_prediction_data, image_width=1000, image_height=400
    )

    assert len(result["entities"]) == 2

    # Entity with ingredients should have fraction_known_ingredients
    first_entity = result["entities"][0]
    assert first_entity["fraction_known_ingredients"] == 0.75

    # Entity without ingredients should not have fraction_known_ingredients
    second_entity = result["entities"][1]
    assert "fraction_known_ingredients" not in second_entity


def test_convert_legacy_all_with_ingredients():
    image_prediction_data = {
        "entities": [
            {
                "end": 50,
                "lang": {"lang": "en", "confidence": 0.9},
                "text": "water, salt",
                "score": 0.8,
                "start": 0,
                "raw_end": 50,
                "ingredients_n": 2,
                "known_ingredients_n": 2,
                "unknown_ingredients_n": 0,
                "bounding_box": [100, 100, 200, 200],
            },
            {
                "end": 100,
                "lang": {"lang": "fr", "confidence": 0.8},
                "text": "sucre",
                "score": 0.7,
                "start": 51,
                "raw_end": 100,
                "ingredients_n": 1,
                "known_ingredients_n": 0,
                "unknown_ingredients_n": 1,
                "bounding_box": [200, 200, 300, 300],
            },
        ]
    }

    result = convert_legacy_ingredient_image_prediction_data(
        image_prediction_data, image_width=400, image_height=400
    )

    # Check that both entities have fraction_known_ingredients
    assert len(result["entities"]) == 2

    first_entity = result["entities"][0]
    assert first_entity["fraction_known_ingredients"] == 1.0  # 2/2

    second_entity = result["entities"][1]
    assert second_entity["fraction_known_ingredients"] == 0.0  # 0/1
