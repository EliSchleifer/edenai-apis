from io import BufferedReader
from pathlib import Path
from typing import Dict
import requests
import json
from time import time
from edenai_apis.features import ProviderInterface, AudioInterface
from edenai_apis.features.audio import (
    SpeechToTextAsyncDataClass,
    SpeechDiarizationEntry,
    SpeechDiarization,
)
from edenai_apis.utils.types import (
    AsyncBaseResponseType,
    AsyncLaunchJobResponseType,
    AsyncPendingResponseType,
    AsyncResponseType,
)
from edenai_apis.utils.exception import ProviderException
from edenai_apis.loaders.data_loader import ProviderDataEnum
from edenai_apis.loaders.loaders import load_provider
from apis.amazon.helpers import check_webhook_result

from apis.amazon.config import storage_clients
from edenai_apis.utils.upload_s3 import upload_file_to_s3


class DeepgramApi(ProviderInterface, AudioInterface):
    provider_name = "deepgram"

    def __init__(self, api_keys: Dict = {}) -> None:
        self.api_settings = load_provider(
            ProviderDataEnum.KEY, self.provider_name, api_keys=api_keys
        )
        self.api_key = self.api_settings["deepgram_key"]
        self.url = "https://api.deepgram.com/v1/listen"

        self.webhook_settings = load_provider(ProviderDataEnum.KEY, "webhooksite")
        self.webhook_token = self.webhook_settings["webhook_token"]
        self.webhook_url = f"https://webhook.site/{self.webhook_token}"

    def audio__speech_to_text_async__launch_job(
        self,
        file: str,
        language: str,
        speakers: int,
        profanity_filter: bool,
        vocabulary: list,
        audio_attributes: tuple,
        model: str,
        file_url: str = "",
    ) -> AsyncLaunchJobResponseType:
        export_format, channels, frame_rate = audio_attributes

        file_name = str(int(time())) + "_" + str(file.split("/")[-1])

        content_url = file_url
        if not content_url:
            content_url = upload_file_to_s3(
                file, Path(file_name).stem + "." + export_format
            )

        headers = {
            "authorization": f"Token {self.api_key}",
            "content-type": f"application/json",
        }

        data = {"url": content_url}

        data_config = {
            "language": language,
            "callback": self.webhook_url,
            "punctuate": "true",
            "diarize": "true",
            "profanity_filter": "false",
            "tier": model,
        }
        if profanity_filter:
            data_config.update({"profanity_filter": "true"})

        if not language:
            del data_config["language"]
            data_config.update({"detect_language": "true"})
        for key, value in data_config.items():
            self.url = (
                f"{self.url}&{key}={value}"
                if "?" in self.url
                else f"{self.url}?{key}={value}"
            )

        response = requests.post(self.url, headers=headers, json=data)
        result = response.json()
        if response.status_code != 200:
            raise ProviderException(
                f"{result.get('err_code')}: {result.get('err_msg')}",
                code = response.status_code
            )

        transcribe_id = response.json()["request_id"]
        transcribe_id = (
            f"{transcribe_id}1"
            if data_config["profanity_filter"] == "true"
            else f"{transcribe_id}0"
        )
        return AsyncLaunchJobResponseType(provider_job_id=f"{transcribe_id}")

    def audio__speech_to_text_async__get_job_result(
        self, provider_job_id: str
    ) -> AsyncBaseResponseType[SpeechToTextAsyncDataClass]:
        public_provider_job_id = provider_job_id
        profanity = provider_job_id[-1]
        provider_job_id = provider_job_id[:-1]
        # Getting results from webhook.site
        wehbook_result, response_status = check_webhook_result(
            provider_job_id, self.webhook_settings
        )

        if response_status != 200:
            raise ProviderException(wehbook_result, code = response_status)
        try:
            original_response = json.loads(wehbook_result[0]["content"])
        except Exception:
            original_response = None
        if original_response is None:
            return AsyncPendingResponseType[SpeechToTextAsyncDataClass](
                provider_job_id=public_provider_job_id
            )

        text = ""
        diarization_entries = []
        speakers = set()

        if original_response.get("err_code"):
            raise ProviderException(
                f"{original_response.get('err_code')}: {original_response.get('err_msg')}",
                code = response_status
            )

        if not "results" in original_response:
            return AsyncPendingResponseType[SpeechToTextAsyncDataClass](
                provider_job_id=public_provider_job_id
            )

        channels = original_response["results"].get("channels", [])
        for channel in channels:
            text_response = channel["alternatives"][0]
            text = text + text_response["transcript"]
            for word in text_response.get("words", []):
                speaker = word.get("speaker", 0) + 1
                speakers.add(speaker)
                diarization_entries.append(
                    SpeechDiarizationEntry(
                        segment=word["word"],
                        speaker=speaker,
                        start_time=str(word["start"]),
                        end_time=str(word["end"]),
                        confidence=word["confidence"],
                    )
                )

        diarization = SpeechDiarization(
            total_speakers=len(speakers), entries=diarization_entries
        )
        if int(profanity) == 1:
            diarization.error_message = (
                "Profanity Filter converts profanity to the nearest "
                "recognized non-profane word or removes it from the transcript completely"
            )
        standardized_response = SpeechToTextAsyncDataClass(
            text=text.strip(), diarization=diarization
        )
        return AsyncResponseType(
            original_response=original_response,
            standardized_response=standardized_response,
            provider_job_id=public_provider_job_id,
        )
