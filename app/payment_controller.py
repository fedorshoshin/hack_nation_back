import ssl

import aiohttp
import certifi
from nostr_sdk import (
    LookupInvoiceRequest,
    LookupInvoiceResponse,
    MakeInvoiceRequest,
    MakeInvoiceResponse,
    NostrWalletConnectUri,
    Nwc,
    PayInvoiceRequest,
)

NWC_AGENT = "nostr+walletconnect://3a2ed56fb15afe4b480a5b43028c60a8b8244e5d8dbe54af729402051993cde4?relay=wss://relay.getalby.com&relay=wss://relay2.getalby.com&secret=440b09c6c9d49c94f5eada6be17c447e0ea3462c6a747e94882b4534e2c5566a&lud16=nwc1777162286675@getalby.com"
NWC_PLATFORM = "nostr+walletconnect://085a2adbcd31786dd9afdf34f0ae6d35819783c2c4e1152535daeb7a81b4d162?relay=wss://relay.getalby.com&relay=wss://relay2.getalby.com&secret=9b02c4252ff07269ec27d697af6a77818947ef0fb2a55a42d521e492f57cc9ae&lud16=nwc1777162368083@getalby.com"

ssl_ctx = ssl.create_default_context(cafile=certifi.where())


async def invoice_from_lnaddress(ln_address: str, amount_sats: int) -> str:
    user, domain = ln_address.split("@")
    url = f"https://{domain}/.well-known/lnurlp/{user}"

    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"LNURL fetch failed: {response.status} {url}")
            metadata = await response.json()

        min_sats = metadata["minSendable"] // 1000
        max_sats = metadata["maxSendable"] // 1000
        if not (min_sats <= amount_sats <= max_sats):
            raise ValueError(
                f"Amount {amount_sats} sats outside range [{min_sats}-{max_sats}]"
            )

        async with session.get(
            metadata["callback"],
            params={"amount": amount_sats * 1000},
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Invoice request failed: {response.status}")
            data = await response.json()

    invoice = data.get("pr")
    if not invoice:
        raise RuntimeError(f"No invoice in LNURL response: {data}")
    return invoice


class PaymentController:
    def __init__(self, agent_uri: str, platform_uri: str):
        self.agent = Nwc(NostrWalletConnectUri.parse(agent_uri))
        self.platform = Nwc(NostrWalletConnectUri.parse(platform_uri))

    @staticmethod
    def _sats(msats: int) -> int:
        return msats // 1000

    async def check_balance(self, account: str) -> int:
        wallet = self._wallet(account)
        balance_msats = await wallet.get_balance()
        return self._sats(balance_msats)

    async def create_invoice(
        self,
        account: str,
        amount_sats: int,
        description: str,
    ) -> MakeInvoiceResponse:
        if amount_sats <= 0:
            raise ValueError("Amount must be greater than zero.")

        wallet = self._wallet(account)
        return await wallet.make_invoice(
            MakeInvoiceRequest(
                amount=amount_sats * 1000,
                description=description,
                description_hash=None,
                expiry=None,
            )
        )

    async def lookup_invoice_status(
        self,
        account: str,
        *,
        payment_hash: str | None = None,
        invoice: str | None = None,
    ) -> LookupInvoiceResponse:
        if not payment_hash and not invoice:
            raise ValueError("Provide either payment_hash or invoice.")

        wallet = self._wallet(account)
        return await wallet.lookup_invoice(
            LookupInvoiceRequest(payment_hash=payment_hash, invoice=invoice)
        )

    async def payout_to_lnaddress(
        self,
        from_account: str,
        ln_address: str,
        amount_sats: int,
    ) -> str:
        if amount_sats <= 0:
            raise ValueError("Amount must be greater than zero.")

        sender = self._wallet(from_account)
        invoice = await invoice_from_lnaddress(ln_address, amount_sats)
        pay_response = await sender.pay_invoice(
            PayInvoiceRequest(id=None, invoice=invoice, amount=None)
        )
        return pay_response.preimage

    def _wallet(self, account: str) -> Nwc:
        if account == "agent":
            return self.agent
        if account == "platform":
            return self.platform
        raise ValueError("Unknown account. Use 'agent' or 'platform'.")


payment_controller = PaymentController(NWC_AGENT, NWC_PLATFORM)
