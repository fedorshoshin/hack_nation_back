import asyncio
import ssl
import certifi
import aiohttp
from nostr_sdk import (
    Nwc,
    NostrWalletConnectUri,
    MakeInvoiceRequest,
    MakeInvoiceResponse,
    PayInvoiceRequest,
    LookupInvoiceRequest,
    LookupInvoiceResponse,
    TransactionState,
)

ssl_ctx = ssl.create_default_context(cafile=certifi.where())

NWC_AGENT = "nostr+walletconnect://3a2ed56fb15afe4b480a5b43028c60a8b8244e5d8dbe54af729402051993cde4?relay=wss://relay.getalby.com&relay=wss://relay2.getalby.com&secret=440b09c6c9d49c94f5eada6be17c447e0ea3462c6a747e94882b4534e2c5566a&lud16=nwc1777162286675@getalby.com"
NWC_PLATFORM = "nostr+walletconnect://085a2adbcd31786dd9afdf34f0ae6d35819783c2c4e1152535daeb7a81b4d162?relay=wss://relay.getalby.com&relay=wss://relay2.getalby.com&secret=9b02c4252ff07269ec27d697af6a77818947ef0fb2a55a42d521e492f57cc9ae&lud16=nwc1777162368083@getalby.com"

TESTER_LN_ADDRESS = "nwc1777164250778@getalby.com"

SESSION_SATS = 500


async def invoice_from_lnaddress(ln_address: str, amount_sats: int) -> str:
    user, domain = ln_address.split("@")
    url = f"https://{domain}/.well-known/lnurlp/{user}"

    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as s:

        async with s.get(url) as r:
            if r.status != 200:
                raise RuntimeError(f"LNURL fetch failed: {r.status} {url}")
            meta = await r.json()

        min_sats = meta["minSendable"] // 1000
        max_sats = meta["maxSendable"] // 1000
        if not (min_sats <= amount_sats <= max_sats):
            raise ValueError(
                f"Сумма {amount_sats} sats вне диапазона [{min_sats}–{max_sats}]"
            )

        async with s.get(meta["callback"],
                         params={"amount": amount_sats * 1000}) as r:
            if r.status != 200:
                raise RuntimeError(f"Invoice request failed: {r.status}")
            data = await r.json()

    pr = data.get("pr")
    if not pr:
        raise RuntimeError(f"Нет invoice в ответе: {data}")
    return pr


class PaymentController:
    def __init__(self, agent_uri: str, platform_uri: str):
        self.agent = Nwc(NostrWalletConnectUri.parse(agent_uri))
        self.platform = Nwc(NostrWalletConnectUri.parse(platform_uri))

    @staticmethod
    def _sats(msats: int) -> int:
        return msats // 1000

    async def check_balance(self, account: str) -> int:
        if account == "agent":
            balance_msats = await self.agent.get_balance()
        elif account == "platform":
            balance_msats = await self.platform.get_balance()
        else:
            raise ValueError("Unknown account. Use 'agent' or 'platform'.")
        return self._sats(balance_msats)

    async def create_invoice(self, account: str, amount_sats: int, description: str) -> MakeInvoiceResponse:
        if account not in {"agent", "platform"}:
            raise ValueError("Unknown account. Use 'agent' or 'platform'.")
        if amount_sats <= 0:
            raise ValueError("Amount must be greater than zero.")

        wallet = self.agent if account == "agent" else self.platform
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
        if account not in {"agent", "platform"}:
            raise ValueError("Unknown account. Use 'agent' or 'platform'.")
        if not payment_hash and not invoice:
            raise ValueError("Provide either payment_hash or invoice.")

        wallet = self.agent if account == "agent" else self.platform
        return await wallet.lookup_invoice(
            LookupInvoiceRequest(payment_hash=payment_hash, invoice=invoice)
        )

    async def wait_until_invoice_paid(
        self,
        account: str,
        *,
        payment_hash: str | None = None,
        invoice: str | None = None,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 120.0,
    ) -> LookupInvoiceResponse:
        started_at = asyncio.get_event_loop().time()
        while True:
            status = await self.lookup_invoice_status(
                account, payment_hash=payment_hash, invoice=invoice
            )
            if status.state == TransactionState.SETTLED:
                return status
            if status.state in {TransactionState.EXPIRED, TransactionState.FAILED}:
                raise RuntimeError(f"Invoice is {status.state.name.lower()}.")

            elapsed = asyncio.get_event_loop().time() - started_at
            if elapsed >= timeout_seconds:
                raise TimeoutError("Timed out waiting for invoice to be paid.")
            await asyncio.sleep(poll_interval_seconds)

    async def send_money(self, from_account: str, to_account: str, amount_sats: int) -> str:
        if from_account not in {"agent", "platform"} or to_account not in {"agent", "platform"}:
            raise ValueError("Unknown account. Use 'agent' or 'platform'.")
        if from_account == to_account:
            raise ValueError("Sender and receiver accounts must be different.")
        if amount_sats <= 0:
            raise ValueError("Amount must be greater than zero.")

        sender = self.agent if from_account == "agent" else self.platform

        invoice = await self.create_invoice(
            to_account,
            amount_sats,
            f"Transfer {from_account} -> {to_account}",
        )
        pay_res = await sender.pay_invoice(
            PayInvoiceRequest(id=None, invoice=invoice.invoice, amount=None)
        )
        return pay_res.preimage

    async def payout_to_lnaddress(self, from_account: str, ln_address: str, amount_sats: int) -> str:
        if from_account not in {"agent", "platform"}:
            raise ValueError("Unknown account. Use 'agent' or 'platform'.")
        if amount_sats <= 0:
            raise ValueError("Amount must be greater than zero.")

        sender = self.agent if from_account == "agent" else self.platform
        invoice = await invoice_from_lnaddress(ln_address, amount_sats)
        pay_res = await sender.pay_invoice(PayInvoiceRequest(id=None, invoice=invoice, amount=None))
        return pay_res.preimage


async def main():
    controller = PaymentController(NWC_AGENT, NWC_PLATFORM)

    print("=" * 55)
    print("PaymentController test run")
    print("=" * 55)

    agent_before = await controller.check_balance("agent")
    platform_before = await controller.check_balance("platform")
    print(f"[before] agent={agent_before} sats, platform={platform_before} sats")

    print(f"\n[test 1] send_money(agent -> platform, {SESSION_SATS} sats)")
    transfer_preimage = await controller.send_money("agent", "platform", SESSION_SATS)
    print(f"  transfer preimage: {transfer_preimage[:35]}...")

    print("\n[test 2] create invoice and wait until paid")
    tracked_amount = 50
    invoice = await controller.create_invoice(
        "platform",
        tracked_amount,
        "Tracked test invoice",
    )
    print(f"  created invoice: {invoice.invoice[:55]}...")

    wait_kwargs = {"invoice": invoice.invoice}
    if invoice.payment_hash:
        wait_kwargs = {"payment_hash": invoice.payment_hash}

    wait_task = asyncio.create_task(
        controller.wait_until_invoice_paid(
            "platform",
            timeout_seconds=120.0,
            poll_interval_seconds=2.0,
            **wait_kwargs,
        )
    )

    await asyncio.sleep(1)
    await controller.agent.pay_invoice(
        PayInvoiceRequest(id=None, invoice=invoice.invoice, amount=None)
    )
    status = await wait_task
    print(f"  invoice state: {status.state.name}")
    print(f"  settled_at: {status.settled_at}")

    agent_after = await controller.check_balance("agent")
    platform_after = await controller.check_balance("platform")
    print(f"\n[after]  agent={agent_after} sats, platform={platform_after} sats")
    print(f"[delta]  agent={agent_after - agent_before:+} sats, "
          f"platform={platform_after - platform_before:+} sats")


if __name__ == "__main__":
    asyncio.run(main())