"""VOICEVOX・Discord SoundboardのHTTP Clientのテスト。

本物のVOICEVOXやDiscordには接続せず、テストの中で127.0.0.1に立てる
aiohttpのサーバーへ接続する。トークンはダミーだけを使う。
"""

import json
import os
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from clients.discord_soundboard_client import DiscordSoundboardClient
from clients.voicevox_client import VoicevoxClient, VoicevoxGenerationError
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_SOUND_ID,
  DUMMY_TOKEN,
  DUMMY_VOICE_CHANNEL_ID,
  RecordingNotifier,
  TempDirTestCase,
)


FAKE_WAV = b"RIFF-dummy-wav"


class LocalServerTestCase(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  """ルートを登録したローカルのHTTPサーバーを立てる。"""

  async def start_server(self, routes):
    app = web.Application()
    app.add_routes(routes)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    self.addAsyncCleanup(server.close)
    return str(server.make_url("")).rstrip("/")


class VoicevoxClientTest(LocalServerTestCase):
  async def asyncSetUp(self):
    self.requests = []
    self.audio_query_failures = 0
    self.audio_query_status = 200

    async def version(request):
      return web.Response(text='"0.0.0-test"\n')

    async def audio_query(request):
      self.requests.append(("audio_query", dict(request.query)))
      if self.audio_query_failures > 0:
        self.audio_query_failures -= 1
        request.transport.close()
        return web.Response(text="")
      if self.audio_query_status != 200:
        return web.Response(status=self.audio_query_status)
      return web.json_response({"speedScale": 1.0, "accent_phrases": []})

    async def synthesis(request):
      body = json.loads(await request.text())
      self.requests.append(("synthesis", dict(request.query), body))
      return web.Response(body=FAKE_WAV, content_type="audio/wav")

    base_url = await self.start_server([
      web.get("/version", version),
      web.post("/audio_query", audio_query),
      web.post("/synthesis", synthesis),
    ])
    self.notifier = RecordingNotifier()
    self.client = VoicevoxClient(
      base_url,
      tmp_dir=os.path.join(self.tmp_dir, "tmp"),
      error_notifier=self.notifier,
    )
    self.addAsyncCleanup(self.client.close)

  async def test_check_health_returns_engine_version(self):
    self.assertEqual(await self.client.check_health(), '"0.0.0-test"')

  async def test_generate_writes_wav_with_requested_settings(self):
    path = await self.client.generate(
      "こんにちは", DUMMY_GUILD_ID, 10, speaker=3, speed=1.5, volume=0.8
    )
    self.assertEqual(path, os.path.join(self.tmp_dir, "tmp", f"{DUMMY_GUILD_ID}-10.wav"))
    with open(path, "rb") as file:
      self.assertEqual(file.read(), FAKE_WAV)

    kind, query = self.requests[0]
    self.assertEqual((kind, query), ("audio_query", {"text": "こんにちは", "speaker": "3"}))
    kind, params, body = self.requests[1]
    self.assertEqual(kind, "synthesis")
    self.assertEqual(params, {"speaker": "3"})
    self.assertEqual(body["speedScale"], 1.5)
    self.assertEqual(body["volumeScale"], 0.8)

  async def test_temporary_disconnect_is_retried(self):
    self.audio_query_failures = 1
    path = await self.client.generate("こんにちは", DUMMY_GUILD_ID, 11)
    self.assertIsNotNone(path)
    self.assertEqual([r[0] for r in self.requests].count("audio_query"), 2)
    self.assertEqual(self.notifier.exceptions, [])

  async def test_gives_up_after_retry_window_and_reports_once(self):
    self.client.RETRY_WINDOW_SECONDS = 1.0
    self.audio_query_failures = 1000
    path = await self.client.generate("こんにちは", DUMMY_GUILD_ID, 12)
    self.assertIsNone(path)
    self.assertEqual(len(self.notifier.exceptions), 1)
    error, context = self.notifier.exceptions[0]
    self.assertIsInstance(error, VoicevoxGenerationError)
    self.assertGreaterEqual(error.attempts, 2)
    self.assertEqual(context, "VOICEVOX generation")

  async def test_http_error_is_not_retried(self):
    self.audio_query_status = 400
    path = await self.client.generate("こんにちは", DUMMY_GUILD_ID, 13)
    self.assertIsNone(path)
    self.assertEqual(len(self.requests), 1)
    self.assertEqual(len(self.notifier.exceptions), 1)

  def test_cleanup_removes_only_wav_files(self):
    tmp_dir = os.path.join(self.tmp_dir, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    for name in ("old.wav", "OLD2.WAV", "keep.txt"):
      with open(os.path.join(tmp_dir, name), "wb") as file:
        file.write(b"x")
    self.assertEqual(self.client.cleanup_tmp_wav_files(), 2)
    self.assertEqual(os.listdir(tmp_dir), ["keep.txt"])


class DiscordSoundboardClientTest(LocalServerTestCase):
  async def asyncSetUp(self):
    self.requests = []

    async def list_sounds(request):
      self.requests.append(
        ("list", request.match_info["guild_id"], request.headers.get("Authorization"))
      )
      return web.json_response({"items": [
        {"sound_id": 1, "name": "いち"},
        {"sound_id": "2", "name": "に"},
      ]})

    async def send_sound(request):
      self.requests.append((
        "play",
        request.match_info["channel_id"],
        request.headers.get("Authorization"),
        await request.json(),
      ))
      return web.Response(status=204)

    base_url = await self.start_server([
      web.get("/guilds/{guild_id}/soundboard-sounds", list_sounds),
      web.post("/channels/{channel_id}/send-soundboard-sound", send_sound),
    ])
    self.client = DiscordSoundboardClient(DUMMY_TOKEN)
    self.client.API_BASE = base_url
    self.addAsyncCleanup(self.client.close)

  async def test_list_sounds(self):
    sounds = await self.client.list_sounds(DUMMY_GUILD_ID)
    self.assertEqual(sounds, [("1", "いち"), ("2", "に")])
    self.assertEqual(
      self.requests,
      [("list", str(DUMMY_GUILD_ID), f"Bot {DUMMY_TOKEN}")],
    )

  async def test_play(self):
    await self.client.play(DUMMY_VOICE_CHANNEL_ID, int(DUMMY_SOUND_ID))
    self.assertEqual(self.requests, [(
      "play",
      str(DUMMY_VOICE_CHANNEL_ID),
      f"Bot {DUMMY_TOKEN}",
      {"sound_id": DUMMY_SOUND_ID},
    )])


if __name__ == "__main__":
  unittest.main()
