from gui_agents.s3.core.engine import LMMEngineAnthropic, LMMEngineOpenAI, PROVIDERS
from gui_agents.s3.utils.images import encode_image, to_bytes


def make_engine(engine_params):
    engine_type = engine_params.get("engine_type")
    if engine_type == "anthropic":
        return LMMEngineAnthropic(**engine_params)
    if engine_type in PROVIDERS:
        return LMMEngineOpenAI(**engine_params)
    raise ValueError(f"engine_type '{engine_type}' is not supported")


class LMMAgent:
    """A conversation with one model. Messages are kept in the OpenAI format;
    the Anthropic engine converts them when it sends."""

    def __init__(self, engine_params=None, system_prompt=None, engine=None):
        if engine is None:
            if engine_params is None:
                raise ValueError("engine_params must be provided")
            engine = make_engine(engine_params)
        self.engine = engine
        # Longest side of images sent to this model (0 = unchanged)
        self.image_max_side = (engine_params or {}).get("image_max_side", 0)
        self.messages = []
        self.add_system_prompt(system_prompt or "You are a helpful assistant.")

    def reset(self):
        self.messages = [self._system()]

    def _system(self):
        return {
            "role": "system",
            "content": [{"type": "text", "text": self.system_prompt}],
        }

    def add_system_prompt(self, system_prompt):
        self.system_prompt = system_prompt
        if self.messages:
            self.messages[0] = self._system()
        else:
            self.messages.append(self._system())

    def remove_message_at(self, index):
        if index < len(self.messages):
            self.messages.pop(index)

    def _content(self, text_content, image_content, image_detail):
        content = [{"type": "text", "text": text_content}]
        if image_content is None or (
            not isinstance(image_content, list) and len(image_content) == 0
        ):
            return content
        images = image_content if isinstance(image_content, list) else [image_content]
        for image in images:
            url = encode_image(to_bytes(image), self.image_max_side)
            content.append(
                {"type": "image_url", "image_url": {"url": url, "detail": image_detail}}
            )
        return content

    def replace_message_at(
        self, index, text_content, image_content=None, image_detail="high"
    ):
        if index < len(self.messages):
            self.messages[index] = {
                "role": self.messages[index]["role"],
                "content": self._content(text_content, image_content, image_detail),
            }

    def add_message(
        self,
        text_content,
        image_content=None,
        role=None,
        image_detail="high",
        put_text_last=False,
    ):
        if role != "user":
            role = "assistant" if self.messages[-1]["role"] == "user" else "user"
        content = self._content(text_content, image_content, image_detail)
        if put_text_last:
            content.append(content.pop(0))
        self.messages.append({"role": role, "content": content})

    def get_response(
        self,
        user_message=None,
        messages=None,
        temperature=0.0,
        max_new_tokens=None,
        use_thinking=False,
        **kwargs,
    ):
        if messages is None:
            messages = self.messages
        if user_message:
            messages.append(
                {"role": "user", "content": [{"type": "text", "text": user_message}]}
            )
        generate = (
            self.engine.generate_with_thinking if use_thinking else self.engine.generate
        )
        return generate(
            messages, temperature=temperature, max_new_tokens=max_new_tokens, **kwargs
        )
