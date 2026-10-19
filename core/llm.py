import os
import time


LOCAL_API_BASE_ENV = "LOCAL_API_BASE"
LOCAL_API_KEY_ENV = "LOCAL_API_KEY"

PROVIDERS_REQUIRING_KEY = ("openai", "google")

_HF_CACHE = {}


def detect_provider(model_name):
    """Map a model string to a backend."""
    name = model_name.lower()
    if name.startswith("hf/"):
        return "hf"
    if name.startswith("local/"):
        return "local"
    if "gpt" in name or name.startswith(("o1", "o3", "o4")):
        return "openai"
    if "gemini" in name:
        return "google"
    if os.environ.get(LOCAL_API_BASE_ENV):
        return "local"
    return "hf"


_SAMPLING_REJECTIONS = (
    "max_tokens",
    "unsupported_value",
    "unsupported parameter",
    "does not support",
)


def _rejects_sampling_parameters(message):
    lowered = message.lower()
    return any(marker in lowered for marker in _SAMPLING_REJECTIONS)


def _strip_prefix(model_name, prefix):
    return model_name[len(prefix):] if model_name.lower().startswith(prefix) else model_name


class ChatGPT:
    DEFAULT_TEMPERATURE = 0

    def __init__(self, model_name, key, system_message=None, temperature=None, base_url=None):
        from openai import OpenAI
        self.model_name = model_name
        self.system_message = system_message
        self.temperature = self.DEFAULT_TEMPERATURE if temperature is None else temperature
        self.restricted_sampling = False
        self.client = OpenAI(api_key=key, base_url=base_url) if base_url else OpenAI(api_key=key)

    def _messages(self, prompt):
        return [
            {
                "role": "system",
                "content": self.system_message
                if self.system_message is not None
                else "You are a helpful assistant.",
            },
            {"role": "user", "content": prompt},
        ]

    def _request_kwargs(self, prompt, end_str):
        if self.restricted_sampling:
            return {
                "model": self.model_name,
                "messages": self._messages(prompt),
                "max_completion_tokens": 1024,
            }
        return {
            "model": self.model_name,
            "messages": self._messages(prompt),
            "temperature": self.temperature,
            "top_p": 1,
            "max_tokens": 1024,
            "n": 1,
            "stop": end_str,
        }

    def generate(self, prompt, options=None, end_str=None):
        retry_num = 0
        retry_limit = 3
        last_error = None
        while retry_num < retry_limit:
            try:
                response = self.client.chat.completions.create(
                    **self._request_kwargs(prompt, end_str)
                )
                return response.choices[0].message.content
            except Exception as e:
                last_error = str(e)
                print(last_error, flush=True)
                if "maximum context length" in last_error.lower():
                    raise RuntimeError(last_error) from e
                if not self.restricted_sampling and _rejects_sampling_parameters(last_error):
                    self.restricted_sampling = True
                    continue
                time.sleep(10)
                retry_num += 1
        raise Exception(last_error)


class Local(ChatGPT):
    """Checkpoint served behind an OpenAI-compatible endpoint, such as vLLM."""

    DEFAULT_TEMPERATURE = 0

    def __init__(self, model_name, key=None, system_message=None, temperature=None):
        base_url = os.environ.get(LOCAL_API_BASE_ENV)
        if not base_url:
            raise ValueError(
                f"Set {LOCAL_API_BASE_ENV} to the OpenAI-compatible endpoint serving {model_name}."
            )
        key = key or os.environ.get(LOCAL_API_KEY_ENV) or "EMPTY"
        super().__init__(
            _strip_prefix(model_name, "local/"), key, system_message, temperature, base_url
        )


class Gemini:
    DEFAULT_TEMPERATURE = 1.0

    def __init__(self, model_name, key, system_message=None, temperature=None):
        from google import genai
        self.model_name = model_name
        self.system_message = system_message
        self.temperature = self.DEFAULT_TEMPERATURE if temperature is None else temperature
        self.client = genai.Client(api_key=key)

    def generate(self, prompt, options=None, end_str=None):
        from google.genai import types
        combined_prompt = (
            f"{self.system_message}\n\n{prompt}" if self.system_message else prompt
        )
        retry_num = 0
        retry_limit = 3
        last_error = None
        while retry_num < retry_limit:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=combined_prompt,
                    config=types.GenerateContentConfig(
                        temperature=self.temperature,
                        max_output_tokens=1024,
                    ),
                )
                return response.text
            except Exception as e:
                last_error = str(e)
                print(last_error, flush=True)
                time.sleep(10)
                retry_num += 1
        raise Exception(last_error)


def _hf_dtype(torch):
    name = os.environ.get("HF_DTYPE", "bfloat16").lower()
    return {
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float16": torch.float16,
        "fp16": torch.float16,
        "float32": torch.float32,
        "fp32": torch.float32,
        "auto": "auto",
    }.get(name, torch.bfloat16)


def _dtype_kwarg():
    """`torch_dtype` was renamed to `dtype` in transformers 4.56."""
    import transformers
    try:
        major, minor = (int(x) for x in transformers.__version__.split(".")[:2])
    except Exception:
        return "torch_dtype"
    return "dtype" if (major, minor) >= (4, 56) else "torch_dtype"


def _load_in_4bit():
    return os.environ.get("HF_LOAD_IN_4BIT", "").lower() in ("1", "true", "yes")


def load_hf_model(model_name):
    """Load a checkpoint onto the available GPUs, cached for the process."""
    if model_name in _HF_CACHE:
        return _HF_CACHE[model_name]

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    device_map = os.environ.get("HF_DEVICE_MAP", "auto")
    dtype = _hf_dtype(torch)

    kwargs = {"device_map": device_map, _dtype_kwarg(): dtype}
    if token:
        kwargs["token"] = token
    if _load_in_4bit():
        from transformers import BitsAndBytesConfig
        compute_dtype = torch.bfloat16 if dtype == "auto" else dtype
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=compute_dtype,
            bnb_4bit_use_double_quant=True,
        )

    print(
        f"Loading HuggingFace checkpoint {model_name} "
        f"(device_map={device_map}, 4bit={_load_in_4bit()})",
        flush=True,
    )
    tok_kwargs = {"token": token} if token else {}
    tokenizer = AutoTokenizer.from_pretrained(model_name, **tok_kwargs)
    model = AutoModelForCausalLM.from_pretrained(model_name, **kwargs)
    model.eval()
    if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    _HF_CACHE[model_name] = (tokenizer, model)
    return tokenizer, model


def _terminators(tokenizer):
    """End-of-turn ids, including the chat terminator when the vocabulary has one."""
    ids = []
    if tokenizer.eos_token_id is not None:
        ids.append(tokenizer.eos_token_id)
    for extra in ("<|eot_id|>", "<|im_end|>"):
        try:
            token_id = tokenizer.convert_tokens_to_ids(extra)
        except Exception:
            token_id = None
        if isinstance(token_id, int) and token_id >= 0 and token_id not in ids:
            unk = getattr(tokenizer, "unk_token_id", None)
            if unk is None or token_id != unk:
                ids.append(token_id)
    return ids or None


class HuggingFace:
    """Open-weight checkpoint downloaded from the Hub and run locally on GPU."""

    DEFAULT_TEMPERATURE = 0

    def __init__(self, model_name, key=None, system_message=None, temperature=None):
        self.model_name = _strip_prefix(model_name, "hf/")
        self.system_message = system_message
        self.temperature = self.DEFAULT_TEMPERATURE if temperature is None else temperature
        self.max_new_tokens = int(os.environ.get("HF_MAX_NEW_TOKENS", "1024"))
        self.top_p = float(os.environ.get("HF_TOP_P", "0.9"))
        self.max_input_tokens = int(os.environ.get("HF_MAX_INPUT_TOKENS", "0"))
        self.tokenizer, self.model = load_hf_model(self.model_name)
        self.terminators = _terminators(self.tokenizer)

    def _encode(self, prompt):
        messages = []
        if self.system_message:
            messages.append({"role": "system", "content": self.system_message})
        messages.append({"role": "user", "content": prompt})
        try:
            return self.tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, return_tensors="pt"
            )
        except Exception:
            head = f"{self.system_message}\n\n" if self.system_message else ""
            return self.tokenizer(f"{head}{prompt}\n", return_tensors="pt")["input_ids"]

    def generate(self, prompt, options=None, end_str=None):
        import torch

        input_ids = self._encode(prompt)
        if self.max_input_tokens and input_ids.shape[-1] > self.max_input_tokens:
            input_ids = input_ids[:, -self.max_input_tokens:]
        input_ids = input_ids.to(self.model.device)
        attention_mask = torch.ones_like(input_ids)

        gen_kwargs = {
            "max_new_tokens": self.max_new_tokens,
            "pad_token_id": (
                self.tokenizer.pad_token_id
                if self.tokenizer.pad_token_id is not None
                else self.tokenizer.eos_token_id
            ),
            "eos_token_id": self.terminators,
        }
        if self.temperature and self.temperature > 0:
            gen_kwargs.update(do_sample=True, temperature=self.temperature, top_p=self.top_p)
        else:
            gen_kwargs.update(do_sample=False)

        with torch.inference_mode():
            outputs = self.model.generate(
                input_ids, attention_mask=attention_mask, **gen_kwargs
            )

        completion = outputs[0][input_ids.shape[-1]:]
        response = self.tokenizer.decode(completion, skip_special_tokens=True).strip()
        if end_str:
            stops = [end_str] if isinstance(end_str, str) else list(end_str)
            for stop in stops:
                if stop and stop in response:
                    response = response.split(stop)[0].strip()
        return response


def build_llm(model_name, key=None, system_message=None, temperature=None):
    provider = detect_provider(model_name)
    if provider == "openai":
        return ChatGPT(model_name, key, system_message, temperature)
    if provider == "google":
        return Gemini(model_name, key, system_message, temperature)
    if provider == "local":
        return Local(model_name, key, system_message, temperature)
    return HuggingFace(model_name, key, system_message, temperature)
