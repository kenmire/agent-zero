from python.helpers.api import ApiHandler
from flask import Request, Response
from python.helpers import local_tts, runtime, settings
import base64

class Synthesize(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        text = input.get("text", "").strip()
        ctxid = input.get("ctxid", "")
        context = self.get_context(ctxid)

        if not text:
            return {"error": "No text provided"}

        # Allow disabling through settings
        set = settings.get_settings()
        if not set.get("tts_enabled", True):
            return {"error": "TTS disabled"}

        # synthesize
        audio_b64 = await runtime.call_development_function(local_tts.synthesize_base64, text)  # type: ignore[arg-type]
        return {"audio": audio_b64}
