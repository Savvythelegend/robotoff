"""Copied and adapted from https://github.com/vi3k6i5/flashtext (MIT-licensed).

Flashtext library is not maintained anymore, and we needed some bugs to be
fixed (especially https://github.com/vi3k6i5/flashtext/issues/119).
"""

import functools
import io
import os
import string
from pathlib import Path
from typing import Any, Union


class KeywordProcessor:
    """KeywordProcessor

    Attributes:
        _keyword (str): Used as key to store keywords in trie dictionary.
            Defaults to '_keyword_'
        non_word_boundaries (set(str)): Characters that will determine if the
        word is continuing.
            Defaults to set([A-Za-z0-9_])
        keyword_trie_dict (dict): Trie dict built character by character, that
        is used for lookup
            Defaults to empty dictionary
        case_sensitive (boolean): if the search algorithm should be case
        sensitive or not.
            Defaults to False

    Examples:
        >>> # import module
        >>> from robotoff.utils.text import KeywordProcessor
        >>> # Create an object of KeywordProcessor
        >>> keyword_processor = KeywordProcessor()
        >>> # add keywords
        >>> keyword_names = ['NY', 'new-york', 'SF']
        >>> clean_names = ['new york', 'new york', 'san francisco']
        >>> for keyword_name, clean_name in zip(keyword_names, clean_names):
        >>>     keyword_processor.add_keyword(keyword_name, clean_name)
        >>> keywords_found = keyword_processor.extract_keywords(
                'I love SF and NY. new-york is the best.')
        >>> keywords_found
        >>> ['san francisco', 'new york', 'new york']

    Note:
        * loosely based on `Aho-Corasick algorithm
          <https://en.wikipedia.org/wiki/Aho%E2%80%93Corasick_algorithm>`_.
        * Idea came from this `Stack Overflow Question
          <https://stackoverflow.com/questions/44178449/regex-replace-is-taking-time-for-millions-of-documents-how-to-make-it-faster>`_.
    """

    def __init__(self, case_sensitive: bool = False):
        """
        Args:
            case_sensitive (boolean): Keyword search should be case sensitive
            set or not.
                Defaults to False
        """
        self._keyword = "_keyword_"
        self._white_space_chars = set([".", "\t", "\n", "\a", " ", ","])
        self.non_word_boundaries = set(string.digits + string.ascii_letters + "_")
        self.keyword_trie_dict = {}  # type: ignore
        self.case_sensitive = case_sensitive
        self._terms_in_trie = 0

    def __len__(self) -> int:
        """Number of terms present in the keyword_trie_dict

        Returns:
            length : int
                Count of number of distinct terms in trie dictionary.

        """
        return self._terms_in_trie

    def __contains__(self, word: str) -> bool:
        """To check if word is present in the keyword_trie_dict

        Args:
            word : string
                word that you want to check

        Returns:
            status : bool
                If word is present as it is in keyword_trie_dict then we return
                True, else False

        Examples:
            >>> keyword_processor.add_keyword('Big Apple')
            >>> 'Big Apple' in keyword_processor
            >>> # True

        """
        if not self.case_sensitive:
            word = word.lower()
        current_dict = self.keyword_trie_dict
        len_covered = 0
        for char in word:
            if char in current_dict:
                current_dict = current_dict[char]
                len_covered += 1
            else:
                break
        return self._keyword in current_dict and len_covered == len(word)

    def __getitem__(self, word: str) -> str | None:
        """If word is present in keyword_trie_dict return the clean name for
        it.

        Args:
            word : string
                word that you want to check

        Returns:
            keyword : string
                If word is present as it is in keyword_trie_dict then we return
                keyword mapped to it.

        Examples:
            >>> keyword_processor.add_keyword('Big Apple', 'New York')
            >>> keyword_processor['Big Apple']
            >>> # New York
        """
        if not self.case_sensitive:
            word = word.lower()
        current_dict = self.keyword_trie_dict
        len_covered = 0
        for char in word:
            if char in current_dict:
                current_dict = current_dict[char]
                len_covered += 1
            else:
                break
        if self._keyword in current_dict and len_covered == len(word):
            return current_dict[self._keyword]

        return None

    def __setitem__(self, keyword: str, clean_name: Any | None = None) -> bool:
        """To add keyword to the dictionary
        pass the keyword and the clean name it maps to.

        Args:
            keyword : string
                keyword that you want to identify

            clean_name : Any
                clean term for that keyword that you would want to get back in
                return or replace if not provided, keyword will be used as the
                clean name also.

        Examples:
            >>> keyword_processor['Big Apple'] = 'New York'
        """
        status = False
        if clean_name is None and keyword:
            clean_name = keyword

        if keyword and clean_name:
            if not self.case_sensitive:
                keyword = keyword.lower()
            current_dict = self.keyword_trie_dict
            for letter in keyword:
                current_dict = current_dict.setdefault(letter, {})
            if self._keyword not in current_dict:
                status = True
                self._terms_in_trie += 1
            current_dict[self._keyword] = clean_name
        return status

    def __delitem__(self, keyword: str) -> bool:
        """To remove keyword from the dictionary
        pass the keyword and the clean name it maps to.

        Args:
            keyword : string
                keyword that you want to remove if it's present

        Examples:
            >>> keyword_processor.add_keyword('Big Apple')
            >>> del keyword_processor['Big Apple']
        """
        status = False
        if keyword:
            if not self.case_sensitive:
                keyword = keyword.lower()
            current_dict = self.keyword_trie_dict
            character_trie_list = []
            for letter in keyword:
                if letter in current_dict:
                    character_trie_list.append((letter, current_dict))
                    current_dict = current_dict[letter]
                else:
                    # if character is not found, break out of the loop
                    current_dict = None  # type: ignore
                    break
            # remove the characters from trie dict if there are no other
            # keywords with them
            if current_dict and self._keyword in current_dict:
                # we found a complete match for input keyword.
                character_trie_list.append((self._keyword, current_dict))
                character_trie_list.reverse()

                for key_to_remove, dict_pointer in character_trie_list:
                    if len(dict_pointer.keys()) == 1:
                        dict_pointer.pop(key_to_remove)
                    else:
                        # more than one key means more than 1 path.
                        # Delete not required path and keep the other
                        dict_pointer.pop(key_to_remove)
                        break
                # successfully removed keyword
                status = True
                self._terms_in_trie -= 1
        return status

    def __iter__(self):
        """Disabled iteration as get_all_keywords() is the right way to
        iterate."""
        raise NotImplementedError("Please use get_all_keywords() instead")

    def set_non_word_boundaries(self, non_word_boundaries: set[str]) -> None:
        """set of characters that will be considered as part of word.

        Args:
            non_word_boundaries (set(str)):
                Set of characters that will be considered as part of word.

        """
        self.non_word_boundaries = non_word_boundaries

    def add_non_word_boundary(self, character: str) -> None:
        """add a character that will be considered as part of word.

        Args:
            character (char):
                Character that will be considered as part of word.

        """
        self.non_word_boundaries.add(character)

    def add_keyword(self, keyword: str, clean_name: Any | None = None) -> bool:
        """To add one or more keywords to the dictionary
        pass the keyword and the clean name it maps to.

        Args:
            keyword : string
                keyword that you want to identify

            clean_name : Any
                clean term for that keyword that you would want to get back in
                return or replace if not provided, keyword will be used as the
                clean name also.

        Returns:
            status : bool
                The return value. True for success, False otherwise.

        Examples:
            >>> keyword_processor.add_keyword('Big Apple', 'New York')
            >>> # This case 'Big Apple' will return 'New York'
            >>> # OR
            >>> keyword_processor.add_keyword('Big Apple')
            >>> # This case 'Big Apple' will return 'Big Apple'
        """
        return self.__setitem__(keyword, clean_name)

    def remove_keyword(self, keyword: str) -> bool:
        """To remove one or more keywords from the dictionary
        pass the keyword and the clean name it maps to.

        Args:
            keyword : string
                keyword that you want to remove if it's present

        Returns:
            status : bool
                The return value. True for success, False otherwise.

        Examples:
            >>> keyword_processor.add_keyword('Big Apple')
            >>> keyword_processor.remove_keyword('Big Apple')
            >>> # Returns True
            >>> # This case 'Big Apple' will no longer be a recognized keyword
            >>> keyword_processor.remove_keyword('Big Apple')
            >>> # Returns False

        """
        return self.__delitem__(keyword)

    def get_keyword(self, word: str) -> str | None:
        """If word is present in keyword_trie_dict return the clean name for
        it.

        Args:
            word : string
                word that you want to check

        Returns:
            keyword : string
                If word is present as it is in keyword_trie_dict then we return
                keyword mapped to it.

        Examples:
            >>> keyword_processor.add_keyword('Big Apple', 'New York')
            >>> keyword_processor.get('Big Apple')
            >>> # New York
        """
        return self.__getitem__(word)

    def add_keyword_from_file(
        self, keyword_file: Union[Path, str], encoding: str = "utf-8"
    ) -> None:
        """To add keywords from a file

        Args:
            keyword_file : path to keywords file
            encoding : specify the encoding of the file

        Examples:
            keywords file format can be like:

            >>> # Option 1: keywords.txt content
            >>> # java_2e=>java
            >>> # java programing=>java
            >>> # product management=>product management
            >>> # product management techniques=>product management

            >>> # Option 2: keywords.txt content
            >>> # java
            >>> # python
            >>> # c++

            >>> keyword_processor.add_keyword_from_file('keywords.txt')

        Raises:
            IOError: If `keyword_file` path is not valid

        """
        if not os.path.isfile(keyword_file):
            raise IOError("Invalid file path {}".format(keyword_file))
        with io.open(keyword_file, encoding=encoding) as f:
            for line in f:
                if "=>" in line:
                    keyword, clean_name = line.split("=>")
                    self.add_keyword(keyword, clean_name.strip())
                else:
                    keyword = line.strip()
                    self.add_keyword(keyword)

    def add_keywords_from_dict(self, keyword_dict: dict[str, str]) -> None:
        """To add keywords from a dictionary

        Args:
            keyword_dict (dict): A dictionary with `str` key and (list `str`)
            as value

        Examples:
            >>> keyword_dict = {
                    "java": ["java_2e", "java programing"],
                    "product management": ["PM", "product manager"]
                }
            >>> keyword_processor.add_keywords_from_dict(keyword_dict)

        Raises:
            AttributeError: If value for a key in `keyword_dict` is not a list.

        """
        for clean_name, keywords in keyword_dict.items():
            if not isinstance(keywords, list):
                raise AttributeError(
                    "Value of key {} should be a list".format(clean_name)
                )

            for keyword in keywords:
                self.add_keyword(keyword, clean_name)

    def remove_keywords_from_dict(self, keyword_dict: dict[str, str]):
        """To remove keywords from a dictionary

        Args:
            keyword_dict (dict): A dictionary with `str` key and (list `str`)
            as value

        Examples:
            >>> keyword_dict = {
                    "java": ["java_2e", "java programing"],
                    "product management": ["PM", "product manager"]
                }
            >>> keyword_processor.remove_keywords_from_dict(keyword_dict)

        Raises:
            AttributeError: If value for a key in `keyword_dict` is not a list.

        """
        for clean_name, keywords in keyword_dict.items():
            if not isinstance(keywords, list):
                raise AttributeError(
                    "Value of key {} should be a list".format(clean_name)
                )

            for keyword in keywords:
                self.remove_keyword(keyword)

    def add_keywords_from_list(self, keyword_list: list[str]) -> None:
        """To add keywords from a list

        Args:
            keyword_list (list(str)): List of keywords to add

        Examples:
            >>> keyword_processor.add_keywords_from_list(["java", "python"]})
        Raises:
            AttributeError: If `keyword_list` is not a list.

        """
        if not isinstance(keyword_list, list):
            raise AttributeError("keyword_list should be a list")

        for keyword in keyword_list:
            self.add_keyword(keyword)

    def remove_keywords_from_list(self, keyword_list: list[str]) -> None:
        """To remove keywords present in list

        Args:
            keyword_list (list(str)): List of keywords to remove

        Examples:
            >>> keyword_processor.remove_keywords_from_list(
                ["java", "python"]})
        Raises:
            AttributeError: If `keyword_list` is not a list.

        """
        if not isinstance(keyword_list, list):
            raise AttributeError("keyword_list should be a list")

        for keyword in keyword_list:
            self.remove_keyword(keyword)

    def get_all_keywords(
        self, term_so_far: str = "", current_dict: dict | None = None
    ) -> dict:
        """Recursively builds a dictionary of keywords present in the
        dictionary.
        And the clean name mapped to those keywords.

        Args:
            term_so_far : string
                term built so far by adding all previous characters
            current_dict : dict
                current recursive position in dictionary

        Returns:
            terms_present : dict
                A map of key and value where each key is a term in the
                keyword_trie_dict. And value mapped to it is the clean name
                mapped to it.

        Examples:
            >>> keyword_processor = KeywordProcessor()
            >>> keyword_processor.add_keyword('j2ee', 'Java')
            >>> keyword_processor.add_keyword('Python', 'Python')
            >>> keyword_processor.get_all_keywords()
            >>> {'j2ee': 'Java', 'python': 'Python'}
            >>> # NOTE: for case_insensitive all keys will be lowercased.
        """
        terms_present = {}
        if not term_so_far:
            term_so_far = ""
        if current_dict is None:
            current_dict = self.keyword_trie_dict
        for key in current_dict:
            if key == "_keyword_":
                terms_present[term_so_far] = current_dict[key]
            else:
                sub_values = self.get_all_keywords(term_so_far + key, current_dict[key])
                for key in sub_values:
                    terms_present[key] = sub_values[key]
        return terms_present

    def extract_keywords(
        self, sentence: str, span_info: bool = False, max_cost: int = 0
    ) -> list[Union[Any, tuple[Any, int, int]]]:
        """Searches in the string for all keywords present in corpus.
        Keywords present are added to a list `keywords_extracted` and returned.

        Args:
            sentence (str): Line of text where we will search for keywords
            span_info (bool): True if you need to span the boundaries where the
            extraction has been performed max_cost (int): maximum levensthein
            distance to accept when extracting keywords

        Returns:
            keywords_extracted (list(str)): List of terms/keywords found in
            sentence that match our corpus

        Examples:
            >>> from robotoff.utils.text import KeywordProcessor
            >>> keyword_processor = KeywordProcessor()
            >>> keyword_processor.add_keyword('Big Apple', 'New York')
            >>> keyword_processor.add_keyword('Bay Area')
            >>> keywords_found = keyword_processor.extract_keywords(
                'I love Big Apple and Bay Area.')
            >>> keywords_found
            >>> ['New York', 'Bay Area']
            >>> keywords_found = keyword_processor.extract_keywords(
                'I love Big Aple and Baay Area.', max_cost=1)
            >>> keywords_found
            >>> ['New York', 'Bay Area']
        """
        keywords_extracted: list[Union[Any, tuple[Any, int, int]]] = []
        if not sentence:
            # if sentence is empty or none just return empty list
            return keywords_extracted

        index_mapping = get_index_mapping(sentence, self.case_sensitive)
        get_span_indices = functools.partial(
            _get_span_indices, index_mapping=index_mapping
        )
        if not self.case_sensitive:
            sentence = sentence.lower()
        current_dict = self.keyword_trie_dict
        sequence_start_pos = 0
        sequence_end_pos = 0
        reset_current_dict = False
        idx = 0
        sentence_len = len(sentence)
        curr_cost = max_cost
        while idx < sentence_len:
            char = sentence[idx]
            # when we reach a character that might denote word end
            if char not in self.non_word_boundaries:

                # if end is present in current_dict
                if self._keyword in current_dict or char in current_dict:
                    # update longest sequence found
                    sequence_found = None
                    longest_sequence_found = None
                    is_longer_seq_found = False
                    if self._keyword in current_dict:
                        sequence_found = current_dict[self._keyword]
                        longest_sequence_found = current_dict[self._keyword]
                        sequence_end_pos = idx

                    # re look for longest_sequence from this position
                    if char in current_dict:
                        current_dict_continued = current_dict[char]

                        idy = idx + 1
                        while idy < sentence_len:
                            inner_char = sentence[idy]
                            if (
                                inner_char not in self.non_word_boundaries
                                and self._keyword in current_dict_continued
                            ):
                                # update longest sequence found
                                longest_sequence_found = current_dict_continued[
                                    self._keyword
                                ]
                                sequence_end_pos = idy
                                is_longer_seq_found = True
                            if inner_char in current_dict_continued:
                                current_dict_continued = current_dict_continued[
                                    inner_char
                                ]
                            elif curr_cost > 0:
                                next_word = self.get_next_word(sentence[idy:])
                                current_dict_continued, cost, _ = next(
                                    self.levensthein(
                                        next_word,
                                        max_cost=curr_cost,
                                        start_node=current_dict_continued,
                                    ),
                                    ({}, 0, 0),
                                )  # current_dict_continued to empty dict by default, so next iteration goes to a `break`
                                curr_cost -= cost
                                idy += len(next_word) - 1
                                if not current_dict_continued:
                                    break
                            else:
                                break
                            idy += 1
                        else:
                            # end of sentence reached.
                            if self._keyword in current_dict_continued:
                                # update longest sequence found
                                longest_sequence_found = current_dict_continued[
                                    self._keyword
                                ]
                                sequence_end_pos = idy
                                is_longer_seq_found = True
                        if is_longer_seq_found:
                            idx = sequence_end_pos
                    current_dict = self.keyword_trie_dict
                    if longest_sequence_found:
                        keywords_extracted.append(
                            (  # type: ignore
                                longest_sequence_found,
                                *get_span_indices(sequence_start_pos, idx),
                            )
                        )
                        curr_cost = max_cost
                    reset_current_dict = True
                else:
                    # we reset current_dict
                    current_dict = self.keyword_trie_dict
                    reset_current_dict = True
            elif char in current_dict:
                # we can continue from this char
                current_dict = current_dict[char]
            elif curr_cost > 0:
                next_word = self.get_next_word(sentence[idx:])
                current_dict, cost, _ = next(
                    self.levensthein(
                        next_word, max_cost=curr_cost, start_node=current_dict
                    ),
                    (self.keyword_trie_dict, 0, 0),
                )
                curr_cost -= cost
                idx += len(next_word) - 1
            else:
                # we reset current_dict
                current_dict = self.keyword_trie_dict
                reset_current_dict = True
                # skip to end of word
                idy = idx + 1
                while idy < sentence_len:
                    char = sentence[idy]
                    if char not in self.non_word_boundaries:
                        break
                    idy += 1
                idx = idy
            # if we are end of sentence and have a sequence discovered
            if idx + 1 >= sentence_len:
                if self._keyword in current_dict:
                    sequence_found = current_dict[self._keyword]
                    keywords_extracted.append(
                        (
                            sequence_found,
                            *get_span_indices(sequence_start_pos, sentence_len),
                        )
                    )
            idx += 1
            if reset_current_dict:
                reset_current_dict = False
                sequence_start_pos = idx
        if span_info:
            return keywords_extracted
        return [value[0] for value in keywords_extracted]

    def get_next_word(self, sentence: str) -> str:
        """Retrieve the next word in the sequence Iterate in the string until
        finding the first char not in non_word_boundaries

        Args:
            sentence (str): Line of text where we will look for the next word

        Returns:
            next_word (str): The next word in the sentence
        Examples:
            >>> from robotoff.utils.text import KeywordProcessor
            >>> keyword_processor = KeywordProcessor()
            >>> keyword_processor.add_keyword('Big Apple')
            >>> 'Big'
        """
        next_word = str()
        for char in sentence:
            if char not in self.non_word_boundaries:
                break
            next_word += char
        return next_word

    def levensthein(self, word: str, max_cost: int = 2, start_node: dict | None = None):
        """Retrieve the nodes where there is a fuzzy match,
        via levenshtein distance, and with respect to max_cost

        Args:
            word (str): word to find a fuzzy match for max_cost (int): maximum
            levenshtein distance when performing the fuzzy match start_node
            (dict): Trie node from which the search is performed

        Yields:
            node, cost, depth (tuple): A tuple containing the final node,
                                      the cost (i.e the distance), and the
                                      depth in the trie

        Examples:
            >>> from robotoff.utils.text import KeywordProcessor
            >>> keyword_processor = KeywordProcessor(case_sensitive=True)
            >>> keyword_processor.add_keyword('Marie', 'Mary')
            >>> next(keyword_processor.levensthein('Maria', max_cost=1))
            >>> ({'_keyword_': 'Mary'}, 1, 5)
            ...
            >>> keyword_processor = KeywordProcessor(case_sensitive=True
            >>> keyword_processor.add_keyword('Marie Blanc', 'Mary')
            >>> next(keyword_processor.levensthein('Mari', max_cost=1))
            >>> ({' ': {'B': {'l': {'a': {
                'n': {'c': {'_keyword_': 'Mary'}}}}}}}, 1, 5)
        """
        start_node = start_node or self.keyword_trie_dict
        rows = range(len(word) + 1)

        for char, node in start_node.items():
            yield from self._levenshtein_rec(char, node, word, rows, max_cost, depth=1)

    def _levenshtein_rec(self, char, node, word, rows, max_cost, depth=0):
        n_columns = len(word) + 1
        new_rows = [rows[0] + 1]
        cost = 0

        for col in range(1, n_columns):
            insert_cost = new_rows[col - 1] + 1
            delete_cost = rows[col] + 1
            replace_cost = rows[col - 1] + int(word[col - 1] != char)
            cost = min((insert_cost, delete_cost, replace_cost))
            new_rows.append(cost)

        stop_crit = isinstance(node, dict) and node.keys() & (
            self._white_space_chars | {self._keyword}
        )
        if new_rows[-1] <= max_cost and stop_crit:
            yield node, cost, depth

        elif isinstance(node, dict) and min(new_rows) <= max_cost:
            for new_char, new_node in node.items():
                yield from self._levenshtein_rec(
                    new_char, new_node, word, new_rows, max_cost, depth=depth + 1
                )


def _get_span_indices(
    start_idx: int, end_idx: int, index_mapping: list[int] | None = None
) -> tuple[int, int]:
    """Return the span indices (start index, end_index) by taking into account
    index shift due to lowercasing. See `get_index_mapping` for further
    explanations.

    :param start_idx: start index of the match
    :param end_idx: end index of the match
    :param index_mapping: optional index mapping, defaults to None
    :return: a (start_idx, end_idx) tuple, possibly shifted if `index_mapping`
        is not None
    """
    if index_mapping is None:
        return start_idx, end_idx
    return index_mapping[start_idx], index_mapping[end_idx - 1] + 1


# LATIN CAPITAL LETTER I WITH DOT ABOVE is the only letter than changes length
# when lowercased: see http://www.unicode.org/Public/UNIDATA/SpecialCasing.txt
LATIN_CAPITAL_LETTER_I_WITH_DOT_ABOVE = "İ"


def get_index_mapping(sentence: str, case_sensitive: bool) -> list[int] | None:
    """Get character index mapping (a list of indices of the same length as
    the lowercased version of `sentence` or None).

    When lowercasing a string, the string changes length if it contains LATIN
    CAPITAL LETTER I WITH DOT ABOVE (`İ`): the length of the lowercased
    version of this letter is 2 (instead of 1).
    If `case_sensitive=True` or if there is no `İ` in the string, this function
    returns None: we don't to account for character index shift during keyword
    extraction.
    Otherwise, we return a list of indices of the same length as the lowercased
    version of `sentence`, that gives the character index in the original
    sentence.

    :param sentence: the original non-lowercased sentence
    :param case_sensitive: whether the keyword extraction is case sensitive
    """
    if case_sensitive or LATIN_CAPITAL_LETTER_I_WITH_DOT_ABOVE not in sentence:
        return None
    offsets = []
    for idx, char in enumerate(sentence):
        if char == LATIN_CAPITAL_LETTER_I_WITH_DOT_ABOVE:
            offsets.append(idx)
        offsets.append(idx)
    return offsets
