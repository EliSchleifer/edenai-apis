from typing import Optional, List, Dict, Sequence
import requests
from edenai_apis.features import ProviderInterface, TextInterface
from edenai_apis.features.text import (
    GenerationDataClass,
    ItemCustomClassificationDataClass,
    CustomClassificationDataClass,
    SummarizeDataClass,
    CustomNamedEntityRecognitionDataClass,
    EmbeddingsDataClass,
    EmbeddingDataClass
)
from edenai_apis.features.text.spell_check.spell_check_dataclass import (
    SpellCheckDataClass,
    SpellCheckItem,
    SuggestionItem
)
from edenai_apis.loaders.data_loader import ProviderDataEnum
from edenai_apis.loaders.loaders import load_provider
from edenai_apis.utils.exception import ProviderException
from edenai_apis.utils.types import ResponseType
from edenai_apis.utils.conversion import construct_word_list
import json


class CohereApi(ProviderInterface, TextInterface):
    provider_name = "cohere"

    def __init__(self, api_keys: Dict = {}):
        self.api_settings = load_provider(
            ProviderDataEnum.KEY, self.provider_name, api_keys=api_keys
        )
        self.api_key = self.api_settings["api_key"]
        self.base_url = "https://api.cohere.ai/"
        self.headers = {
            "accept": "application/json",
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
            "Cohere-Version": "2022-12-06",
        }

    def _calculate_summarize_length(output_sentences: int):
        if output_sentences < 3:
            return "short"
        elif output_sentences < 6:
            return "medium"
        elif output_sentences > 6:
            return "long"

    def _format_custom_ner_examples(example: Dict):
        # Get the text
        text = example["text"]

        # Get the entities
        entities = example["entities"]

        # Create an empty list to store the extracted entities
        extracted_entities = []

        # Loop through the entities and extract the relevant information
        for entity in entities:
            category = entity["category"]
            entity_name = entity["entity"]

            # Append the extracted entity to the list
            extracted_entities.append({"entity": entity_name, "category": category})

        # Create the string with the extracted entities
        return f"""
        Text: #{text}#
        Answer: "[{', '.join([f'{{"entity":"{entity["entity"]}", "category":"{entity["category"]}"}}' for entity in extracted_entities])}]"
        ---
            """

    def _format_spell_check_prompt(text: str, language: str) -> str:
        return f"""
Given a text with spelling errors, identify the misspelled words and correct them. 
Return the results as a list of dictionaries, where each dictionary contains two keys: "word" and "correction". 
The "word" key should contain the misspelled word, and the "correction" key should contain the corrected version of the word. 
For example, if the misspelled word is 'halo', the corresponding dictionary should be: {{"word": "halo", "correction": "hello"}}.
Text : {text}
List of corrected words :
"""


    def text__generation(
        self,
        text: str,
        max_tokens: int,
        temperature: float,
        model: str,
    ) -> ResponseType[GenerationDataClass]:
        url = f"{self.base_url}generate"

        payload = {
            "prompt": text,
            "model": model,
            "temperature": temperature,
            "stop_sequences": ["--"],
            "frequency_penalty": 0.3,
            "truncate": "END",
        }

        if max_tokens != 0:
            payload["max_tokens"] = max_tokens

        response = requests.post(
            url, json=payload, headers=self.headers
        )
        original_response = response.json()

        if "message" in original_response:
            raise ProviderException(
                original_response["message"],
                code = response.status_code
            )

        generated_texts = original_response.get("generations")
        standardized_response = GenerationDataClass(
            generated_text=generated_texts[0]["text"]
        )
        return ResponseType[GenerationDataClass](
            original_response=original_response,
            standardized_response=standardized_response,
        )

    def text__custom_classification(
        self, texts: List[str], labels: List[str], examples: List[List[str]]
    ) -> ResponseType[CustomClassificationDataClass]:
        # Build the request
        url = f"{self.base_url}classify"
        example_dict = []
        for example in examples:
            example_dict.append({"text": example[0], "label": example[1]})
        payload = {
            "inputs": texts,
            "examples": example_dict,
            "model": "large",
        }

        response = requests.post(
            url, json=payload, headers=self.headers
        )
        original_response = response.json()

        # Handle provider errors
        if "message" in original_response:
            raise ProviderException(
                original_response["message"],
                code = response.status_code
            )

        # Standardization
        classifications = []
        for classification in original_response.get("classifications"):
            classifications.append(
                ItemCustomClassificationDataClass(
                    input=classification["input"],
                    label=classification["prediction"],
                    confidence=classification["confidence"],
                )
            )

        return ResponseType[CustomClassificationDataClass](
            original_response=original_response,
            standardized_response=CustomClassificationDataClass(
                classifications=classifications
            ),
        )

    def text__summarize(
        self, text: str, output_sentences: int, language: str, model: str
    ) -> ResponseType[SummarizeDataClass]:
        url = f"{self.base_url}summarize"
        length = "long"

        if output_sentences:
            length = CohereApi._calculate_summarize_length(output_sentences)

        payload = {
            "length": length,
            "format": "paragraph",
            "model": model,
            "extractiveness": "low",
            "temperature": 0.0,
            "text": text,
        }

        response = requests.post(
            url, json=payload, headers=self.headers
        )
        original_response = response.json()

        if "message" in original_response:
            raise ProviderException(
                original_response["message"],
                code = response.status_code
            )

        standardized_response = SummarizeDataClass(
            result=original_response.get("summary", {})
        )

        return ResponseType[SummarizeDataClass](
            original_response=original_response,
            standardized_response=standardized_response,
        )

    def text__custom_named_entity_recognition(
        self, text: str, entities: List[str], examples: Optional[List[Dict]] = None
    ) -> ResponseType[CustomNamedEntityRecognitionDataClass]:
        url = f"{self.base_url}generate"

        # Construct the prompt
        built_entities = ",".join(entities)
        prompt_examples = ""
        if examples is not None:
            for example in examples:
                prompt_examples = (
                    prompt_examples + CohereApi._format_custom_ner_examples(example)
                )
        prompt = f"""You act as a named entities recognition model. Extract the specified entities ({built_entities}) from the text enclosed in hash symbols (#) and return a JSON List of dictionaries with two keys: "entity" and "category". The "entity" key represents the detected entity and the "category" key represents the category of the entity.

If no entities are found, return an empty list.

Example :

{prompt_examples}

Text: 
{text}

Answer:"""

        # Construct request
        payload = {
            "model": "command",
            "prompt": prompt,
            "max_tokens": 650,
            "temperature": 0,
            "k": 0,
            "frequency_penalty": 0.3,
            "truncate": "END",
            "stop_sequences": [],
            "return_likelihoods": "NONE",
        }
        response = requests.post(url, json=payload, headers=self.headers)
        if response.status_code != 200:
            raise ProviderException(response.text, response.status_code)

        original_response = response.json()
        data = original_response.get("generations")[0]["text"]
        try:
            items = json.loads(data)
        except (IndexError, KeyError, json.JSONDecodeError) as exc:
            raise ProviderException(
                "An error occurred while parsing the response."
            ) from exc
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except (IndexError, KeyError, json.JSONDecodeError) as exc:
                raise ProviderException(
                    "An error occurred while parsing the response."
                ) from exc
            
        standardized_response = CustomNamedEntityRecognitionDataClass(items=items)

        return ResponseType[CustomNamedEntityRecognitionDataClass](
            original_response=original_response,
            standardized_response=standardized_response,
        )

    def text__spell_check(
        self, text: str, language: str
    ) -> ResponseType[SpellCheckDataClass]:
        url = f"{self.base_url}generate"

        payload = {
            "prompt": CohereApi._format_spell_check_prompt(text, language),
            "model": "command-nightly",
            "max_tokens": 1000,
            "temperature": 0,
            "stop_sequences": ["--"],
            "truncate": "END",
        }

        response = requests.post(
            url, json=payload, headers=self.headers
        )
        original_response = response.json()

        if "message" in original_response:
            raise ProviderException(
                original_response["message"],
                code = response.status_code
            )

        try:
            data = original_response.get("generations")[0]["text"]
            corrected_items = json.loads(data)
        except (json.JSONDecodeError) as exc:
            raise ProviderException(
                "An error occurred while parsing the response."
            ) from exc
            
        corrections = construct_word_list(text, corrected_items)
        items: Sequence[SpellCheckItem] = []
        for item in corrections:
            items.append(
                SpellCheckItem(
                    text=item["word"],
                    offset=item["offset"],
                    length=item["length"],
                    type = None,
                    suggestions=[SuggestionItem(suggestion=item["suggestion"], score = 1.0)],
                )
            )
        return ResponseType[SpellCheckDataClass](
            original_response=original_response,
            standardized_response=SpellCheckDataClass(text=text, items=items),
        )

    def text__embeddings(
        self, 
        texts: List[str],
        model: str) -> ResponseType[EmbeddingsDataClass]:
        url = f"{self.base_url}embed"
        model = model.split("__")
        payload = {
            "texts" : texts,
            "model" : model[1]
        }
        response = requests.post(url, json = payload, headers=self.headers)
        original_response = response.json()
        if "message" in original_response:
            raise ProviderException(
                original_response["message"],
                code = response.status_code
            )
        
        items: Sequence[EmbeddingsDataClass] = []
        for prediction in original_response["embeddings"]:
            items.append(EmbeddingDataClass(embedding=prediction))

        standardized_response = EmbeddingsDataClass(items=items)
        return ResponseType[EmbeddingsDataClass](
            original_response=original_response,
            standardized_response=standardized_response,
        )