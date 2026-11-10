class GeminiBackend:
    def __init__(self, gemini_client):
        self.client = gemini_client
    def generate(self, prompt: str, lang: str, context: dict) -> str:
        if lang == "hi":
            lang_instruction = (
                "\nLanguage policy: reply in pure Hindi, not Hinglish. "
                "Do not mix Hindi and English words unless the user explicitly asks for a switch or code-mixing. "
                "Keep the sentence structure, vocabulary, and tone natural for Hindi."
            )
        else:
            lang_instruction = (
                "\nRespond in language code: {lang}. Match the user's language unless they ask you to switch."
            ).format(lang=lang)
        system_prompt = context.get("system_prompt", "") + lang_instruction
        return self.client.generate(
            prompt=prompt,
            lang=lang,
            context={**context, "system_prompt": system_prompt},
        )