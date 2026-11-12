"""Exercise the dependency interfaces used by the bot without external services."""

import asyncio
import io
import json
import unittest
from unittest.mock import patch

import httpx2
import ijson
from motor.motor_asyncio import AsyncIOMotorClient
from openai import APIConnectionError, AsyncOpenAI
from telegram import Bot
from telegram.ext import Application
from telegram.ext._utils.webhookhandler import WebhookAppClass
from telegram.request import HTTPXRequest
from telegramify_markdown import markdownify
from telegramify_markdown.config import get_runtime_config
from tornado.testing import AsyncHTTPTestCase

from whaleyeah.database import init_database, mob
from whaleyeah.plugins.openai_compatible.openaibot_compatible import OpenAICompatibleBot


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_markdown_and_global_heading_configuration(self):
        self.assertEqual(markdownify("**hello**"), "*hello*")
        self.assertIn("print(1)", markdownify("```python\nprint(1)\n```"))
        self.assertIn("minutes", markdownify("Wait ~5 minutes; price $10.49 (~$11.20)"))
        config = get_runtime_config()
        heading = config.markdown_symbol.heading_level_1
        expandable = config.cite_expandable
        try:
            config.markdown_symbol.heading_level_1 = "H1"
            config.cite_expandable = False
            self.assertIn("H1", markdownify("# Title"))
            self.assertIn("quoted", markdownify("> quoted"))
        finally:
            config.markdown_symbol.heading_level_1 = heading
            config.cite_expandable = expandable

    def test_streaming_json_preserves_unicode_and_null_values(self):
        documents = [{"text": "你好", "reply_to_message_id": None}, {"id": 42}]
        stream = io.BytesIO(json.dumps({"messages": documents}).encode("utf-8"))
        self.assertEqual(list(ijson.items(stream, "messages.item")), documents)

    def test_motor_client_and_collections_initialize_without_an_event_loop(self):
        previous = mob.__dict__.copy()
        client = AsyncIOMotorClient("mongodb://127.0.0.1:27017", connect=False)
        self.addCleanup(client.close)
        try:
            with patch("whaleyeah.database.AsyncIOMotorClient", return_value=client):
                init_database({
                    "uri": "mongodb://127.0.0.1:27017",
                    "db_name": "compatibility",
                    "IWAKU_GROUP_ID": -1001234567890,
                })
            self.assertEqual(mob.GROUP_ID, -1001234567890)
            self.assertEqual(mob.history.full_name, "compatibility.history")
            self.assertEqual(mob.tokens.full_name, "compatibility.tokens")
            cursor = mob.history.find({"tokens": "hello"}).sort("_id", -1).limit(10)
            self.assertIs(cursor.collection, mob.history)
        finally:
            mob.__dict__.clear()
            mob.__dict__.update(previous)


class OpenAICompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_and_responses_with_and_without_streaming(self):
        for api_type in ["chat", "responses"]:
            for stream in [False, True]:
                with self.subTest(api_type=api_type, stream=stream):
                    requests = []

                    def serve(request):
                        requests.append(request)
                        if api_type == "chat":
                            payload = {
                                "id": "chat-test", "object": "chat.completion", "created": 1,
                                "model": "test-model", "choices": [{
                                    "index": 0, "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": "Hello world"},
                                }],
                            }
                            events = [{
                                "id": "chat-test", "object": "chat.completion.chunk", "created": 1,
                                "model": "test-model", "choices": [{
                                    "index": 0, "delta": {"content": part}, "finish_reason": None,
                                }],
                            } for part in ["Hello", " world"]]
                        else:
                            payload = {
                                "id": "resp-test", "object": "response", "created_at": 1,
                                "model": "test-model", "status": "completed", "output": [{
                                    "type": "message", "id": "msg-test", "role": "assistant",
                                    "status": "completed", "content": [{
                                        "type": "output_text", "text": "Hello world", "annotations": [],
                                    }],
                                }],
                            }
                            events = [{
                                "type": "response.output_text.delta", "delta": part,
                                "item_id": "msg-test", "output_index": 0, "content_index": 0,
                                "sequence_number": index,
                            } for index, part in enumerate(["Hello", " world"])]
                        if not stream:
                            return httpx2.Response(200, json=payload)
                        content = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
                        return httpx2.Response(200, headers={"Content-Type": "text/event-stream"},
                                              content=content + "data: [DONE]\n\n")

                    async with AsyncOpenAI(
                        api_key="test-key", base_url="https://example.invalid/v1",
                        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(serve)),
                    ) as client:
                        with patch("whaleyeah.plugins.openai_compatible.openaibot_compatible.AsyncOpenAI",
                                   return_value=client):
                            bot = OpenAICompatibleBot({
                                "api_key": "test-key", "model": "test-model", "api_type": api_type,
                                "endpoint": "https://example.invalid/v1",
                                "stream": stream, "create_params": {
                                    "temperature": 0.5, "extra_body": {"custom_option": True},
                                },
                            })
                        messages = [{"role": "user", "content": "hi"}]
                        text = "".join([part async for part in bot._stream_response_text(messages)])
                    self.assertEqual(text, "Hello world")
                    self.assertEqual(len(requests), 1)
                    request = requests[0]
                    expected_path = "/v1/responses" if api_type == "responses" else "/v1/chat/completions"
                    self.assertEqual(request.url.path, expected_path)
                    body = json.loads(request.content)
                    self.assertEqual(body["input" if api_type == "responses" else "messages"], messages)
                    self.assertEqual(body["temperature"], 0.5)
                    self.assertTrue(body["custom_option"])
                    self.assertEqual(body.get("stream", False), stream)

    async def test_sdk_stream_closes_when_generation_is_aborted_or_fails(self):
        class Body(httpx2.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"Hello","item_id":"msg1","output_index":0,"content_index":0,"sequence_number":0}\n\n'
                yield b'data: {"type":"error","message":"stream failed","code":"server_error","param":null}\n\n'

            async def aclose(self):
                self.closed = True

        for abort in [False, True]:
            with self.subTest(abort=abort):
                body = Body()
                transport = httpx2.MockTransport(lambda request: httpx2.Response(
                    200, headers={"Content-Type": "text/event-stream"}, stream=body,
                ))
                async with AsyncOpenAI(api_key="test-key", http_client=httpx2.AsyncClient(transport=transport)) as client:
                    with patch("whaleyeah.plugins.openai_compatible.openaibot_compatible.AsyncOpenAI", return_value=client):
                        bot = OpenAICompatibleBot({"api_key": "test-key", "model": "test-model"})
                    generation = bot._stream_response_text([{"role": "user", "content": "hi"}])
                    self.assertEqual(await anext(generation), "Hello")
                    if abort:
                        await generation.aclose()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "stream failed"):
                            await anext(generation)
                    self.assertTrue(body.closed)

    async def test_httpx2_connection_errors_are_reported_by_the_sdk(self):
        def fail(request):
            raise httpx2.ConnectError("connection failed", request=request)

        async with AsyncOpenAI(
            api_key="test-key", max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(fail)),
        ) as client:
            with patch("whaleyeah.plugins.openai_compatible.openaibot_compatible.AsyncOpenAI", return_value=client):
                bot = OpenAICompatibleBot({"api_key": "test-key", "model": "test-model"})
            with self.assertRaises(APIConnectionError):
                _ = [part async for part in bot._stream_response_text([{"role": "user", "content": "hi"}])]

    async def test_telegram_job_queue_executes_a_scheduled_callback(self):
        application = Application.builder().token("123456:test-token").build()
        queue = application.job_queue
        self.assertIsNotNone(queue)
        called = asyncio.Event()

        async def callback(context):
            self.assertEqual(context.job.data, "test-data")
            called.set()

        queue.run_once(callback, when=0, data="test-data")
        await queue.start()
        try:
            await asyncio.wait_for(called.wait(), timeout=5)
        finally:
            await queue.stop()
            for request in application.bot._request:
                await request.shutdown()


class WebhookCompatibilityTests(AsyncHTTPTestCase):
    def get_app(self):
        self.queue = asyncio.Queue()
        self.request = HTTPXRequest()
        bot = Bot("123456:test-token", request=self.request, get_updates_request=self.request)
        return WebhookAppClass("/bot", bot, self.queue, secret_token="test-secret")

    def tearDown(self):
        try:
            self.io_loop.run_sync(self.request.shutdown)
        finally:
            super().tearDown()

    def test_valid_webhook_delivers_a_telegram_update(self):
        response = self.fetch("/bot", method="POST", body=json.dumps({"update_id": 42}), headers={
            "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "test-secret",
        })
        self.assertEqual(response.code, 200)
        self.assertEqual(self.queue.get_nowait().update_id, 42)

    def test_invalid_webhook_secret_does_not_queue_an_update(self):
        response = self.fetch("/bot", method="POST", body=json.dumps({"update_id": 42}), headers={
            "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "wrong-secret",
        })
        self.assertEqual(response.code, 403)
        self.assertTrue(self.queue.empty())
