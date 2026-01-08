"""LLM API client wrapper with provider abstraction."""

from typing import Optional
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config.settings import settings

logger = structlog.get_logger()


class LLMClient:
    """Unified LLM client supporting Gemini, Anthropic, and OpenAI."""

    def __init__(self, provider: str = None, api_key: str = None):
        self.provider = provider or settings.llm_provider
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        """Lazy initialization of the client."""
        if self._client is not None:
            return self._client

        if self.provider == "gemini":
            from google import genai
            api_key = self._api_key or settings.gemini_api_key
            if not api_key:
                raise ValueError("Gemini API key not configured. Set RI_GEMINI_API_KEY environment variable.")
            self._client = genai.Client(api_key=api_key)

        elif self.provider == "anthropic":
            import anthropic
            api_key = self._api_key or settings.anthropic_api_key
            if not api_key:
                raise ValueError("Anthropic API key not configured")
            self._client = anthropic.AsyncAnthropic(api_key=api_key)

        elif self.provider == "openai":
            import openai
            api_key = self._api_key or settings.openai_api_key
            if not api_key:
                raise ValueError("OpenAI API key not configured")
            self._client = openai.AsyncOpenAI(api_key=api_key)

        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10)
    )
    async def complete(
        self,
        prompt: str,
        system: str = None,
        max_tokens: int = None,
        temperature: float = None
    ) -> str:
        """Generate a completion from the LLM."""
        client = self._get_client()
        max_tokens = max_tokens or settings.llm_max_tokens
        temperature = temperature if temperature is not None else settings.llm_temperature

        try:
            if self.provider == "gemini":
                return await self._complete_gemini(client, prompt, system, max_tokens, temperature)
            elif self.provider == "anthropic":
                return await self._complete_anthropic(client, prompt, system, max_tokens, temperature)
            else:
                return await self._complete_openai(client, prompt, system, max_tokens, temperature)

        except Exception as e:
            logger.error("llm_call_failed", provider=self.provider, error=str(e))
            raise

    async def _complete_gemini(self, client, prompt: str, system: str, max_tokens: int, temperature: float) -> str:
        """Call Gemini API."""
        from google.genai import types
        import asyncio

        # Combine system prompt with user prompt
        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"

        # Gemini client is sync, run in executor
        def _sync_call():
            response = client.models.generate_content(
                model=settings.llm_model,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            return response.text

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_call)

    async def _complete_anthropic(self, client, prompt: str, system: str, max_tokens: int, temperature: float) -> str:
        """Call Anthropic API."""
        messages = [{"role": "user", "content": prompt}]
        kwargs = {
            "model": settings.llm_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages
        }
        if system:
            kwargs["system"] = system

        response = await client.messages.create(**kwargs)
        return response.content[0].text

    async def _complete_openai(self, client, prompt: str, system: str, max_tokens: int, temperature: float) -> str:
        """Call OpenAI API."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=settings.llm_model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages
        )
        return response.choices[0].message.content
