import asyncio
import unittest

from data import helpers


class DataHelpersSessionTests(unittest.TestCase):
    def tearDown(self):
        if helpers._session and not helpers._session.closed:
            asyncio.run(helpers.close_session())

    def test_global_aiohttp_session_is_recreated_for_new_event_loop(self):
        async def first_loop():
            return await helpers.get_session()

        first_session = asyncio.run(first_loop())

        async def second_loop():
            second_session = await helpers.get_session()
            try:
                self.assertIsNot(second_session, first_session)
                self.assertIs(second_session._loop, asyncio.get_running_loop())
            finally:
                await helpers.close_session()

        asyncio.run(second_loop())
        self.assertTrue(first_session.closed)


if __name__ == "__main__":
    unittest.main()
