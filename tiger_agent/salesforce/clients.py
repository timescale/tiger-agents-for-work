import logfire
from aiohttp import ClientSession
from aiosfstream_ng.auth import AuthenticatorBase
from simple_salesforce.api import Salesforce

from tiger_agent.salesforce.constants import (
    SALESFORCE_CLIENT_ID,
    SALESFORCE_CLIENT_SECRET,
    SALESFORCE_DOMAIN,
)


class ClientCredentialsAuthenticator(AuthenticatorBase):
    """OAuth2 client credentials authenticator for aiosfstream."""

    async def _authenticate(self):
        async with ClientSession() as session:
            resp = await session.post(
                f"https://{SALESFORCE_DOMAIN}/services/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": SALESFORCE_CLIENT_ID,
                    "client_secret": SALESFORCE_CLIENT_SECRET,
                },
            )
            return resp.status, await resp.json()


def get_salesforce_api_client() -> Salesforce | None:
    if (
        not SALESFORCE_DOMAIN
        or not SALESFORCE_CLIENT_ID
        or not SALESFORCE_CLIENT_SECRET
    ):
        logfire.info("Salesforce is not configured")
        return None

    """Return an authenticated simple-salesforce client."""
    domain = SALESFORCE_DOMAIN.removesuffix(".salesforce.com")
    return Salesforce(
        domain=domain,
        consumer_key=SALESFORCE_CLIENT_ID,
        consumer_secret=SALESFORCE_CLIENT_SECRET,
    )
