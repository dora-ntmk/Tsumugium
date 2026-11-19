"""運営者へのエラー通知（services/error_notification_service.py）のテスト。"""

import asyncio
import unittest

from services.error_notification_service import ErrorNotificationService, operator_ids
from tests.support import DUMMY_OTHER_USER_ID, DUMMY_USER_ID


class FakeUser:
  def __init__(self, error=None):
    self.sent = []
    self.error = error

  async def send(self, *, embed):
    if self.error is not None:
      raise self.error
    self.sent.append(embed)


class FakeClient:
  def __init__(self, users):
    self.users = users

  def get_user(self, user_id):
    return self.users.get(user_id)

  async def fetch_user(self, user_id):
    raise LookupError(user_id)


class OperatorIdsTest(unittest.TestCase):
  def test_normalizes(self):
    self.assertEqual(operator_ids(None), ())
    self.assertEqual(operator_ids(DUMMY_USER_ID), (DUMMY_USER_ID,))
    self.assertEqual(
      operator_ids([DUMMY_USER_ID, DUMMY_OTHER_USER_ID, DUMMY_USER_ID]),
      (DUMMY_USER_ID, DUMMY_OTHER_USER_ID),
    )


class SendTest(unittest.TestCase):
  def test_every_operator_gets_the_error(self):
    users = {DUMMY_USER_ID: FakeUser(), DUMMY_OTHER_USER_ID: FakeUser()}
    service = ErrorNotificationService(FakeClient(users), (DUMMY_USER_ID, DUMMY_OTHER_USER_ID))
    asyncio.run(service._send("boom"))
    for user in users.values():
      [embed] = user.sent
      self.assertIn("boom", embed.description)

  def test_one_failure_does_not_stop_others(self):
    users = {DUMMY_OTHER_USER_ID: FakeUser()}
    service = ErrorNotificationService(FakeClient(users), (DUMMY_USER_ID, DUMMY_OTHER_USER_ID))
    asyncio.run(service._send("boom"))
    self.assertEqual(len(users[DUMMY_OTHER_USER_ID].sent), 1)


if __name__ == "__main__":
  unittest.main()
