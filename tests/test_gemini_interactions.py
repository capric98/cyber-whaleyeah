import base64
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from google import genai
from google.genai import interactions, types

from whaleyeah.plugins.gemini.gemini_bot import GeminiBot
from whaleyeah.plugins.gemini.interactions import InteractionStream
from whaleyeah.plugins.media_extractor import (
    AttachmentDownloadError,
    AttachmentTooLargeError,
    ExtractedAttachment,
    ExtractedMessage,
)


def start(index, step):
    return {"event_type": "step.start", "index": index, "step": step}


def delta(index, content):
    return {"event_type": "step.delta", "index": index, "delta": content}


def stop(index):
    return {"event_type": "step.stop", "index": index}


def completed(status="completed"):
    return {"event_type": "interaction.completed", "interaction": {"id": "test-id", "status": status}}


def text_events(text="Hello"):
    return [
        start(0, {"type": "thought"}),
        delta(0, {"type": "thought_signature", "signature": "YWJj"}),
        stop(0),
        start(1, {"type": "model_output"}),
        delta(1, {"type": "text", "text": text}),
        stop(1),
        completed(),
    ]


class StreamTests(unittest.TestCase):
    def test_sdk_events_preserve_signature_citations_and_media(self):
        annotation = {"type": "url_citation", "url": "https://example.com", "start_index": 0, "end_index": 5}
        image = {"type": "image", "mime_type": "image/png", "data": base64.b64encode(b"image").decode()}
        events = text_events("Hello")
        events[5:5] = [
            delta(1, {"type": "text_annotation_delta", "annotations": [annotation]}),
            delta(1, image),
            delta(1, {"type": "text", "text": " world"}),
        ]
        stream = InteractionStream()
        for event in events:
            event_class = {
                "step.start": interactions.StepStart,
                "step.delta": interactions.StepDelta,
                "step.stop": interactions.StepStop,
                "interaction.completed": interactions.InteractionCompletedEvent,
            }[event["event_type"]]
            stream.consume(event_class.model_validate(event))
        self.assertEqual(stream.text, "Hello world")
        self.assertEqual(stream.image_bytes, b"image")
        self.assertEqual(stream.finish(), [
            {"type": "thought", "signature": "YWJj"},
            {"type": "model_output", "content": [
                {"type": "text", "text": "Hello", "annotations": [annotation]},
                image,
                {"type": "text", "text": " world"},
            ]},
        ])

    def test_tools_retain_call_ids_signatures_and_raw_results(self):
        steps = [
            {"type": "google_search_call", "id": "search1", "arguments": {}},
            {"type": "google_search_result", "call_id": "search1", "result": []},
            {"type": "url_context_call", "id": "url1", "arguments": {}},
            {"type": "url_context_result", "call_id": "url1", "result": []},
            {"type": "code_execution_call", "id": "code1", "arguments": {}},
            {"type": "code_execution_result", "call_id": "code1", "result": ""},
        ]
        deltas = [
            {"type": "google_search_call", "arguments": {"queries": ["test"]}, "signature": "YWJj"},
            {"type": "google_search_result", "result": [{"url": "https://example.com", "title": "Test"}]},
            {"type": "url_context_call", "arguments": {"urls": ["https://example.com"]}},
            {"type": "url_context_result", "result": [{"url": "https://example.com", "status": "success"}]},
            {"type": "code_execution_call", "arguments": {"code": "print(1)", "language": "python"}},
            {"type": "code_execution_result", "result": "1", "is_error": False, "signature": "ZGVm"},
        ]
        stream = InteractionStream()
        for index, (step, content) in enumerate(zip(steps, deltas)):
            for event in [start(index, step), delta(index, content), stop(index)]:
                stream.consume(event)
        stream.consume(completed())
        result = stream.finish()
        self.assertEqual(result[0]["signature"], "YWJj")
        self.assertEqual(result[1]["call_id"], "search1")
        self.assertEqual(result[3]["result"], deltas[3]["result"])
        self.assertEqual(result[5], {"call_id": "code1", **deltas[5]})
        self.assertIn("print(1)", stream.text)
        self.assertIn("执行结果", stream.text)
        self.assertNotIn("https://example.com", stream.text)

    def test_thought_summaries_and_incremental_function_arguments(self):
        stream = InteractionStream()
        for event in [
            start(0, {"type": "thought"}),
            delta(0, {"type": "thought_summary", "content": {"type": "text", "text": "Thinking"}}),
            delta(0, {"type": "thought_signature", "signature": "YWJj"}),
            stop(0),
            start(1, {"type": "function_call", "id": "f1", "name": "weather", "arguments": {}}),
            delta(1, {"type": "arguments_delta", "arguments": '{"city":'}),
            delta(1, {"type": "arguments_delta", "arguments": '"Paris"}'}),
            stop(1),
        ]:
            stream.consume(event)
        self.assertEqual(stream.steps[1]["arguments"], {"city": "Paris"})
        self.assertEqual(stream.steps[0]["summary"], [{"type": "text", "text": "Thinking"}])
        self.assertEqual(stream.text, "")
        with self.assertRaisesRegex(RuntimeError, "requires_action"):
            stream.consume(completed("requires_action"))

    def test_missing_completion_and_unfinished_steps_are_rejected(self):
        stream = InteractionStream()
        for event in text_events()[:-1]:
            stream.consume(event)
        with self.assertRaisesRegex(RuntimeError, "before interaction.completed"):
            stream.finish()
        stream.consume(start(2, {"type": "model_output"}))
        with self.assertRaisesRegex(RuntimeError, "unfinished steps"):
            stream.consume(completed())

    def test_error_and_incomplete_status_are_rejected(self):
        for status in ["failed", "cancelled", "incomplete"]:
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, status):
                InteractionStream().consume(completed(status))
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            InteractionStream().consume({"event_type": "error", "error": {"message": "deadline"}})

    def test_unknown_sdk_wrapper_is_unwrapped_and_returned_history_is_isolated(self):
        stream = InteractionStream()
        for event in text_events():
            stream.consume({"type": "UNKNOWN", "is_unknown": True, "raw": event})
        result = stream.finish()
        result[1]["content"][0]["text"] = "changed"
        self.assertEqual(stream.text, "Hello")


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = text_events()
        self.requests = []

        def serve(request):
            self.requests.append(json.loads(request.content))
            content = "".join(
                f"event: {event['event_type']}\ndata: {json.dumps(event)}\n\n"
                for event in self.events
            ) + "event: done\ndata: [DONE]\n\n"
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=content)

        self.client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(async_client_args={"transport": httpx.MockTransport(serve)}),
        )
        with patch("whaleyeah.plugins.gemini.gemini_bot.genai.Client", return_value=self.client):
            self.bot = GeminiBot({"api_key": "test-key", "memory_size": 2, "tools": ["google_search"]})
        self.bot.whitelist_cache[42] = True
        self.telegram = SimpleNamespace(name="@whale", token="123456:secret")
        self.rich_reply = self.message("Hello", id=100)
        self.reply = AsyncMock(return_value=self.rich_reply)
        self.drafts = AsyncMock()

        async def extract(message, **kwargs):
            text = message.text.removeprefix("/gemini").strip() if kwargs.get("is_command") else message.text
            return ExtractedMessage(text, [])

        self.extraction = AsyncMock(side_effect=extract)
        for target, mock in [
            ("extract_media", self.extraction),
            ("reply_rich_message", self.reply),
            ("send_rich_message_draft", self.drafts),
        ]:
            patcher = patch(f"whaleyeah.plugins.gemini.gemini_bot.{target}", mock)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aio.aclose()
        self.client.close()

    def message(self, text="/gemini hi", id=1, chat_id=10, reply=None):
        return SimpleNamespace(
            text=text, caption=None, id=id, chat_id=chat_id,
            reply_to_message=reply, message_thread_id=None,
            get_bot=lambda: self.telegram,
            reply_chat_action=AsyncMock(),
            reply_text=AsyncMock(return_value=SimpleNamespace(id=200, chat_id=chat_id)),
            reply_photo=AsyncMock(return_value=SimpleNamespace(id=201, chat_id=chat_id)),
        )

    async def call(self, message=None):
        update = SimpleNamespace(
            effective_message=message or self.message(), edited_message=None,
            effective_user=SimpleNamespace(id=42), update_id=123,
            get_bot=lambda: self.telegram,
        )
        await self.bot.get_callback().callback(update, SimpleNamespace())

    async def test_real_sdk_stream_request_and_complete_history_round_trip(self):
        await self.call()
        request = self.requests[0]
        self.assertEqual(request["model"], "gemini-3.5-flash-lite")
        self.assertFalse(request["store"])
        self.assertTrue(request["stream"])
        self.assertEqual(request["tools"], [{"type": "google_search"}])
        self.assertNotIn("previous_interaction_id", request)
        history = deepcopy(self.bot.memory["msg 100 in chat 10"])
        self.assertEqual(history[1], {"type": "thought", "signature": "YWJj"})
        self.assertEqual(history[-1]["content"][0]["text"], "Hello")

        await self.call(self.message("/gemini follow up", reply=self.rich_reply))
        self.assertEqual(self.requests[1]["input"][:-1], history)
        self.assertEqual(self.requests[1]["input"][-1]["content"][0]["text"], "follow up")
        self.assertEqual(self.drafts.await_args.kwargs["markdown"], "")

    async def test_reply_branches_do_not_modify_the_parent_or_each_other(self):
        await self.call()
        parent = deepcopy(self.bot.memory["msg 100 in chat 10"])
        self.reply.return_value = self.message("Branch one", id=101)
        await self.call(self.message("/gemini branch one", reply=self.rich_reply))
        self.assertEqual(self.bot.memory["msg 100 in chat 10"], parent)
        self.reply.return_value = self.message("Branch two", id=102)
        await self.call(self.message("/gemini branch two", reply=self.rich_reply))
        self.assertEqual(self.requests[2]["input"][:-1], parent)
        self.assertNotIn("branch one", json.dumps(self.requests[2]["input"]))
        self.assertNotIn("msg 100 in chat 10", self.bot.memory)
        self.assertEqual(len(self.bot.memory), 2)

    async def test_real_sdk_tool_history_round_trip(self):
        self.events = [
            start(0, {"type": "google_search_call", "id": "s1", "arguments": {"queries": []}}),
            delta(0, {"type": "google_search_call", "arguments": {"queries": ["test"]}, "signature": "YWJj"}),
            stop(0),
            start(1, {"type": "google_search_result", "call_id": "s1", "result": []}),
            delta(1, {"type": "google_search_result", "result": [{"url": "https://example.com", "title": "Test"}]}),
            stop(1),
            start(2, {"type": "code_execution_call", "id": "c1", "arguments": {"code": "", "language": "python"}}),
            delta(2, {"type": "code_execution_call", "arguments": {"code": "print(1)", "language": "python"}, "signature": "ZGVm"}),
            stop(2),
            start(3, {"type": "code_execution_result", "call_id": "c1", "result": ""}),
            delta(3, {"type": "code_execution_result", "result": "1", "is_error": False}),
            stop(3),
            start(4, {"type": "model_output"}),
            delta(4, {"type": "text", "text": "Answer"}),
            stop(4),
            completed(),
        ]
        await self.call()
        history = deepcopy(self.bot.memory["msg 100 in chat 10"])
        self.assertEqual(history[1]["signature"], "YWJj")
        self.assertEqual(history[3]["arguments"]["code"], "print(1)")
        self.assertIn("代码执行", self.reply.await_args.kwargs["markdown"])
        await self.call(self.message("/gemini continue", reply=self.rich_reply))
        self.assertEqual(self.requests[1]["input"][:-1], history)
        self.assertNotIn("代码执行", json.dumps(history, ensure_ascii=False))

    async def test_long_reply_uses_paste_link_but_remembers_the_full_answer(self):
        answer = "x" * 32769
        self.events = text_events(answer)
        message = self.message()
        with patch("whaleyeah.plugins.gemini.gemini_bot.xgg_pb_link", new_callable=AsyncMock, return_value="https://example.com/paste") as paste:
            await self.call(message)
        paste.assert_awaited_once_with(text=answer, title="hi")
        message.reply_text.assert_awaited_once_with("https://example.com/paste")
        self.assertEqual(self.bot.memory["msg 200 in chat 10"][-1]["content"][0]["text"], answer)

    async def test_sdk_error_event_is_reported_and_does_not_enter_memory(self):
        self.events = [*text_events()[:-1], {"event_type": "error", "error": {"code": "gateway_timeout", "message": "deadline expired"}}]
        await self.call()
        self.assertEqual(self.bot.memory, {})
        self.assertIn("deadline expired", self.reply.await_args.kwargs["markdown"])

    async def test_reply_cache_miss_extracts_only_the_replied_message(self):
        await self.call(self.message("/gemini explain", reply=self.message("Original", id=99)))
        self.assertEqual([step["content"][0]["text"] for step in self.requests[0]["input"]], ["Original", "explain"])

    async def test_same_message_id_in_another_chat_does_not_inherit_history(self):
        await self.call()
        await self.call(self.message("/gemini explain", chat_id=11, reply=self.message("Other chat", id=100, chat_id=11)))
        self.assertEqual(self.requests[1]["input"][0]["content"][0]["text"], "Other chat")
        self.assertEqual(len(self.requests[1]["input"]), 2)

    async def test_plain_command_starts_a_new_conversation(self):
        await self.call()
        await self.call(self.message("/gemini new question"))
        self.assertEqual(len(self.requests[1]["input"]), 1)

    async def test_draft_and_rich_reply_failures_fall_back_without_losing_history(self):
        self.drafts.side_effect = RuntimeError("Textdraft_peer_invalid")
        self.reply.side_effect = RuntimeError("Rich messages unavailable")
        message = self.message()
        await self.call(message)
        message.reply_text.assert_awaited_once_with("Hello")
        self.assertIn("msg 200 in chat 10", self.bot.memory)

    async def test_truncated_stream_and_failed_status_do_not_enter_memory(self):
        for events in [text_events()[:-1], [*text_events()[:-1], completed("incomplete")]]:
            self.events = events
            await self.call()
            self.assertEqual(self.bot.memory, {})
            self.assertIn("生成内容时出错", self.reply.await_args.kwargs["markdown"])
            self.assertEqual(self.drafts.await_args.kwargs["markdown"], "")

    async def test_final_reply_failure_does_not_enter_memory(self):
        self.reply.side_effect = RuntimeError("rich failed")
        message = self.message()
        message.reply_text.side_effect = RuntimeError("plain failed")
        with self.assertRaisesRegex(RuntimeError, "plain failed"):
            await self.call(message)
        self.assertEqual(self.bot.memory, {})
        self.assertEqual(self.drafts.await_args.kwargs["markdown"], "")

    async def test_image_only_reply_and_last_image_delivery(self):
        images = [{"type": "image", "mime_type": "image/png", "data": base64.b64encode(value).decode()} for value in [b"first", b"last"]]
        self.events = [start(0, {"type": "model_output"}), *(delta(0, image) for image in images), stop(0), completed()]
        message = self.message()
        await self.call(message)
        message.reply_photo.assert_awaited_once_with(photo=b"last")
        self.reply.assert_not_awaited()
        self.assertEqual(self.bot.memory["msg 201 in chat 10"][-1]["content"], images)

    async def test_attachments_are_base64_encoded_and_preserve_mime_types(self):
        attachments = [
            ExtractedAttachment("photo", "image/jpeg", "a.jpg", b"photo"),
            ExtractedAttachment("audio", "audio/mpeg", "a.mp3", b"audio"),
            ExtractedAttachment("document", "application/pdf", "a.pdf", b"pdf"),
            ExtractedAttachment("document", "text/csv", "a.csv", b"a,b\n1,2"),
        ]
        self.extraction.side_effect = None
        self.extraction.return_value = ExtractedMessage("Describe", attachments)
        await self.call()
        parts = self.requests[0]["input"][0]["content"]
        self.assertEqual([part["type"] for part in parts], ["text", "image", "audio", "document", "document"])
        for attachment, part in zip(attachments, parts[1:]):
            self.assertEqual(part["mime_type"], attachment.mime_type)
            self.assertEqual(base64.b64decode(part["data"]), attachment.bytes)

    async def test_attachment_errors_are_reported_without_an_api_call(self):
        for error in [AttachmentTooLargeError("too large"), AttachmentDownloadError("download failed")]:
            with self.subTest(error=type(error).__name__):
                self.extraction.side_effect = error
                message = self.message()
                await self.call(message)
                self.assertEqual(self.requests, [])
                message.reply_text.assert_awaited_once()

    async def test_memory_is_fifo_and_snapshots_are_owned_by_the_cache(self):
        history = [{"type": "user_input", "content": [{"type": "text", "text": "Original"}]}]
        self.bot.remember("a", history)
        history[0]["content"][0]["text"] = "changed"
        self.bot.remember("b", [])
        self.bot.remember("a", self.bot.memory["a"])
        self.assertEqual(self.bot.memory["a"][0]["content"][0]["text"], "Original")
        self.bot.remember("c", [])
        self.assertEqual(set(self.bot.memory), {"b", "c"})
        self.bot.mem_queue = []
        self.bot.remember("disabled", [])
        self.assertNotIn("disabled", self.bot.memory)

    async def test_model_override_and_legacy_tool_names(self):
        with patch("whaleyeah.plugins.gemini.gemini_bot.genai.Client", return_value=self.client):
            bot = GeminiBot({"model": "custom-model", "tools": [" Tool_Code_Execution ", "URL_CONTEXT", "google_maps"]})
        self.assertEqual(bot.model, "custom-model")
        self.assertEqual(bot.tools, [{"type": name} for name in ["code_execution", "url_context", "google_maps"]])
        self.assertEqual(bot.memory, {})


if __name__ == "__main__":
    unittest.main()
