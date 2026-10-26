"""Anthropic Messages API adapter (API version 2026-10-21).

Only client tools are exposed: searches and writes still go through FieriA's
permission gate. Opaque response blocks live only in the current tool exchange.
No SDK dependency; HTTP transports are shared with the other LLM adapters.
https://platform.claude.com/docs/en/api/messages/create
https://platform.claude.com/docs/en/build-with-claude/streaming
"""
import copy
import json
import re
import urllib.error
import urllib.parse

API_BASE = "https://api.anthropic.com/v1"
API_VERSION = "2026-10-21"
_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _connection_error(exc):
    # Remote bodies and exception strings can echo credentials or user content.
    # Expose only a status and a fixed hint, never raw request/response data.
    status = getattr(exc, "code", None)
    if status is None:
        match = re.match(r"HTTP (\d{3}):", str(exc))
        status = int(match[1]) if match else None
    hints = {
        400: "モデル・思考量・出力上限などの設定を確認してね",
        401: "AnthropicのAPIキーを確認してね",
        402: "AnthropicのAPIクレジットを確認してね",
        403: "APIキーとモデルの利用権限を確認してね",
        404: "モデル名と利用可能なモデル一覧を確認してね",
        413: "添付や会話が大きすぎる。分割してね",
        429: "利用上限・クレジット残高を確認し、少し待って再試行してね",
        529: "Anthropicが混雑している。少し待って再試行してね",
    }
    if status:
        return RuntimeError(f"Anthropic API HTTP {status}: " + hints.get(
            status, "Anthropic側でエラーが発生した。少し待って再試行してね"))
    return RuntimeError("Anthropic APIに接続できない。ネットワークと接続設定を確認してね")


def _events(lines):
    """SSE records, including multiline data and comments. EOF is not success."""
    data = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                try:
                    event = json.loads("\n".join(data))
                except (ValueError, TypeError):
                    raise RuntimeError("Anthropicのストリーム形式が壊れていた") from None
                if not isinstance(event, dict):
                    raise RuntimeError("Anthropicのストリーム形式が壊れていた")
                yield event
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        # Some servers omit the final blank line; still require message_stop.
        try:
            event = json.loads("\n".join(data))
        except (ValueError, TypeError):
            raise RuntimeError("Anthropicのストリーム形式が壊れていた") from None
        if not isinstance(event, dict):
            raise RuntimeError("Anthropicのストリーム形式が壊れていた")
        yield event


def _attachment_blocks(images):
    blocks = []
    for image in images or []:
        mime = image.get("mime", "")
        source = {"type": "base64", "media_type": mime, "data": image.get("b64", "")}
        if mime == "application/pdf":
            blocks.append({"type": "document", "source": source})
        elif mime in _IMAGE_TYPES:
            blocks.append({"type": "image", "source": source})
        else:
            raise ValueError("Claudeに送れない添付形式。PNG・JPEG・GIF・WebP・PDFを使ってね")
    return blocks


def _messages(messages):
    system, converted = [], []
    for message in messages:
        role = message.get("role")
        text = message.get("content") or ""
        if role == "system":
            if text:
                system.append(text)
            continue
        if role == "tool":
            content = [{"type": "text", "text": text or "（結果なし）"}]
            content.extend(_attachment_blocks(message.get("images")))
            blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                       "content": content, "is_error": bool(message.get("is_error", False))}]
            role = "user"
        elif role in ("user", "assistant"):
            opaque = (message.get("provider_content") or {}).get("anthropic")
            if role == "assistant" and opaque is not None:
                blocks = copy.deepcopy(opaque)
            else:
                blocks = [{"type": "text", "text": text}] if text else []
                blocks.extend(_attachment_blocks(message.get("images")))
                for call in message.get("tool_calls") or []:
                    function = call["function"]
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except (ValueError, TypeError):
                        raise ValueError("Claudeへ返すツール引数が壊れていた") from None
                    if not isinstance(args, dict):
                        raise ValueError("Claudeへ返すツール引数が壊れていた")
                    blocks.append({"type": "tool_use", "id": call["id"],
                                   "name": function["name"], "input": args})
        else:
            raise ValueError("Claudeへ送る会話の役割が不正だった")
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    if not converted:
        raise ValueError("Claudeへ送る会話が空だった")
    return "\n\n".join(system), converted


def _result(body):
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        raise RuntimeError("Anthropicの応答形式が不正だった")
    blocks = body["content"]
    text, calls, ids = [], [], set()
    for block in blocks:
        if not isinstance(block, dict):
            raise RuntimeError("Anthropicの応答形式が不正だった")
        if block.get("type") == "text":
            text.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            call_id, name, args = block.get("id"), block.get("name"), block.get("input")
            if (not isinstance(call_id, str) or not call_id or call_id in ids or
                    not isinstance(name, str) or not name or not isinstance(args, dict)):
                raise RuntimeError("Claudeのツール呼び出しが不完全だった。操作は実行しない")
            ids.add(call_id)
            calls.append({"id": call_id, "name": name, "arguments": copy.deepcopy(args)})
    reason = body.get("stop_reason")
    if calls and reason != "tool_use":
        raise RuntimeError("Claudeのツール呼び出しが完了しなかった。出力上限などを確認してね")
    reply = "".join(text).strip()
    if not reply and not calls:
        if reason == "max_tokens":
            raise RuntimeError("Claudeが思考中に出力上限に達した。最大出力トークンを増やしてね")
        if reason == "refusal":
            raise RuntimeError("Claudeがこの依頼への応答を見送った")
        raise RuntimeError("Claudeから回答が返らなかった")
    return {"text": reply, "tool_calls": calls,
            "provider_content": {"anthropic": copy.deepcopy(blocks)}}


class AnthropicLLM:
    supports_native_tools = True

    def __init__(self, entry, api_key, temperature, max_tokens, reasoning_effort=""):
        self.model = entry.get("model", "").strip()
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.reasoning_effort = (reasoning_effort or "").strip().lower()
        self._models = {}
        # temperature is deliberately not retained/sent: removed in this API version.

    def _headers(self):
        if not self.api_key:
            raise RuntimeError("AnthropicのAPIキーが未設定。設定 → モデル → APIキーから登録してね")
        return {"Authorization": f"Bearer {self.api_key}", "anthropic-version": API_VERSION}

    def _get(self, path):
        import llm as transport

        headers = self._headers()
        try:
            return transport._get_json(API_BASE + path, headers=headers)
        except Exception as exc:
            raise _connection_error(exc) from None

    def list_models(self):
        names, seen_cursors = [], set()
        path = "/models?limit=100"
        for _ in range(100):
            body = self._get(path)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise RuntimeError("Anthropicのモデル一覧の形式が不正だった")
            for info in body["data"]:
                if not isinstance(info, dict) or not isinstance(info.get("id"), str):
                    raise RuntimeError("Anthropicのモデル一覧の形式が不正だった")
                model_id = info["id"]
                self._models[model_id] = copy.deepcopy(info)
                if info.get("lifecycle") != "retired" and model_id not in names:
                    names.append(model_id)
            if not body.get("has_more"):
                return names
            cursor = body.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise RuntimeError("Anthropicのモデル一覧の続きが取得できなかった")
            seen_cursors.add(cursor)
            path = "/models?" + urllib.parse.urlencode({"limit": 100, "after_id": cursor})
        raise RuntimeError("Anthropicのモデル一覧が長すぎるため取得を中止した")

    def _info(self):
        if not self.model:
            raise RuntimeError("Claudeのモデル名が未設定。設定画面の一覧から選んでね")
        if self.model not in self._models:
            info = self._get("/models/" + urllib.parse.quote(self.model, safe=""))
            if not isinstance(info, dict) or not isinstance(info.get("id"), str):
                raise RuntimeError("Claudeのモデル情報を取得できなかった")
            self._models[self.model] = copy.deepcopy(info)
        info = self._models[self.model]
        if info.get("lifecycle") == "retired":
            raise RuntimeError("このClaudeモデルは提供終了。モデル一覧から選び直してね")
        return info

    def health(self):
        try:
            self._info()
            return True, f"{self.model} @ Anthropic"
        except Exception as exc:
            return False, str(exc)

    def _payload(self, messages, tools, max_tokens, stream):
        self._headers()  # Fail locally for an unset key, before metadata or message requests.
        info = self._info()
        requested = self.max_tokens if max_tokens is None else max_tokens
        requested = int(requested or 0)
        ceiling = info.get("max_tokens")
        ceiling = ceiling if isinstance(ceiling, int) and ceiling > 0 else None
        # FieriA's 0 means model maximum; Anthropic's 0 means no generation.
        if requested <= 0:
            if ceiling is None:
                raise RuntimeError("モデルの出力上限を取得できない。最大出力トークンを指定してね")
            requested = ceiling
        elif ceiling:
            requested = min(requested, ceiling)
        system, converted = _messages(messages)
        payload = {"model": self.model, "messages": converted,
                   "max_tokens": requested, "stream": stream}
        if system:
            payload["system"] = system
        capabilities = info.get("capabilities") or {}
        thinking = capabilities.get("thinking") or {}
        if ((thinking.get("types") or {}).get("adaptive") or {}).get("supported") is True:
            payload["thinking"] = {"type": "adaptive"}
        effort = capabilities.get("effort") or {}
        if self.reasoning_effort and (effort.get(self.reasoning_effort) or {}).get("supported") is True:
            payload["output_config"] = {"effort": self.reasoning_effort}
        if tools:
            payload["tools"] = [{"name": tool["function"]["name"],
                                 "description": tool["function"].get("description", ""),
                                 "input_schema": copy.deepcopy(tool["function"]["parameters"])}
                                for tool in tools]
            payload["tool_choice"] = {"type": "auto"}
        return payload

    def chat_tools(self, messages, tools, max_tokens=None):
        import llm as transport

        payload = self._payload(messages, tools, max_tokens, False)
        try:
            body = transport._post_json(API_BASE + "/messages", payload, headers=self._headers())
        except Exception as exc:
            raise _connection_error(exc) from None
        return _result(body)

    def chat(self, messages, max_tokens=None):
        return self.chat_tools(messages, [], max_tokens=max_tokens)["text"]

    def _stream(self, messages, tools, max_tokens, should_stop=None):
        import llm as transport

        payload = self._payload(messages, tools, max_tokens, True)
        lines = transport._post_sse_stream(API_BASE + "/messages", payload, headers=self._headers())
        blocks, opened, json_parts = {}, set(), {}
        body = None
        try:
            for event in _events(lines):
                if should_stop and should_stop():
                    return {"text": "", "tool_calls": []}
                kind = event.get("type")
                if kind == "error":
                    raise RuntimeError("Anthropicのストリームでエラーが発生した。少し待って再試行してね")
                if kind == "message_start":
                    if body is not None or not isinstance(event.get("message"), dict):
                        raise RuntimeError("Anthropicのストリーム開始が不正だった")
                    body = copy.deepcopy(event["message"])
                elif kind == "content_block_start":
                    idx = event.get("index")
                    if body is None or idx != len(blocks) or not isinstance(event.get("content_block"), dict):
                        raise RuntimeError("Anthropicのストリーム順序が不正だった")
                    blocks[idx] = copy.deepcopy(event["content_block"])
                    opened.add(idx)
                    if blocks[idx].get("type") == "text" and blocks[idx].get("text"):
                        yield blocks[idx]["text"]
                elif kind == "content_block_delta":
                    idx, delta = event.get("index"), event.get("delta") or {}
                    if idx not in opened:
                        raise RuntimeError("Anthropicのストリーム順序が不正だった")
                    delta_kind = delta.get("type")
                    fields = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature"}
                    if delta_kind in fields:
                        field = fields[delta_kind]
                        blocks[idx][field] = blocks[idx].get(field, "") + delta.get(field, "")
                        if delta_kind == "text_delta" and delta.get(field):
                            yield delta[field]
                    elif delta_kind == "input_json_delta":
                        json_parts.setdefault(idx, []).append(delta.get("partial_json", ""))
                    elif delta_kind == "citations_delta":
                        blocks[idx].setdefault("citations", []).append(copy.deepcopy(delta["citation"]))
                elif kind == "content_block_stop":
                    idx = event.get("index")
                    if idx not in opened:
                        raise RuntimeError("Anthropicのストリーム順序が不正だった")
                    if idx in json_parts:
                        try:
                            blocks[idx]["input"] = json.loads("".join(json_parts[idx]))
                        except (ValueError, TypeError):
                            raise RuntimeError("Claudeのツール引数が不完全だった。操作は実行しない") from None
                    opened.remove(idx)
                elif kind == "message_delta":
                    if body is None:
                        raise RuntimeError("Anthropicのストリーム順序が不正だった")
                    body.update(event.get("delta") or {})
                elif kind == "message_stop":
                    if body is None or opened or not body.get("stop_reason"):
                        raise RuntimeError("Anthropicのストリームが完了しなかった")
                    body["content"] = [blocks[i] for i in sorted(blocks)]
                    return _result(body)
                # ping and future event types are deliberately ignored.
        except (urllib.error.URLError, OSError) as exc:
            raise _connection_error(exc) from None
        except RuntimeError as exc:
            if re.match(r"HTTP \d{3}:", str(exc)):
                raise _connection_error(exc) from None
            raise
        finally:
            if hasattr(lines, "close"):
                lines.close()
        raise RuntimeError("Anthropicのストリームが途中で切れた。操作は実行しない")

    def chat_stream(self, messages, max_tokens=None):
        yield from self._stream(messages, [], max_tokens)

    def chat_tools_stream(self, messages, tools, max_tokens=None, on_delta=None, should_stop=None):
        stream = self._stream(messages, tools, max_tokens, should_stop=should_stop)
        try:
            while True:
                if should_stop and should_stop():
                    return {"text": "", "tool_calls": []}
                try:
                    delta = next(stream)
                except StopIteration as done:
                    return done.value
                if on_delta:
                    try:
                        on_delta(delta)
                    except Exception:
                        pass
        finally:
            stream.close()
