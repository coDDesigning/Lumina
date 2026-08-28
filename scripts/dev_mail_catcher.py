"""A local SMTP sink for trying the verification flow without a real relay.

It accepts any message on 127.0.0.1:1025 and appends it to a file, so a
registration performed against a development server produces a verification
link you can open. It speaks the minimum of SMTP, offers no TLS and no
authentication, and refuses to listen on anything but the loopback address:
it is a development aid, never a relay.

    python scripts/dev_mail_catcher.py

Then run the API with EMAIL_VERIFICATION_REQUIRED=true, SMTP_HOST=127.0.0.1,
SMTP_PORT=1025, and SMTP_USE_TLS=false. See docs/authentication.md.
"""

import argparse
import asyncio
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 1025
DEFAULT_MAILDROP = Path("data") / "maildrop.txt"
MESSAGE_SEPARATOR = "=== end of message ===\n"


class MailCatcher:
    def __init__(self, maildrop: Path) -> None:
        self._maildrop = maildrop

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        async def reply(line: str) -> None:
            writer.write(f"{line}\r\n".encode())
            await writer.drain()

        await reply("220 lumina-dev-mail-catcher ESMTP")
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                command = raw.decode("utf-8", "replace").strip().upper()
                if command.startswith("EHLO"):
                    await reply("250-lumina-dev-mail-catcher")
                    await reply("250 HELP")
                elif command.startswith("DATA"):
                    await reply("354 End data with <CR><LF>.<CR><LF>")
                    self._store(await self._read_message(reader))
                    await reply("250 Queued")
                elif command.startswith("QUIT"):
                    await reply("221 Bye")
                    break
                else:
                    # HELO, MAIL, RCPT, RSET, NOOP: nothing here inspects them.
                    await reply("250 OK")
        finally:
            writer.close()

    @staticmethod
    async def _read_message(reader: asyncio.StreamReader) -> str:
        lines: list[str] = []
        while True:
            chunk = await reader.readline()
            if not chunk or chunk.rstrip(b"\r\n") == b".":
                return "".join(lines)
            lines.append(chunk.decode("utf-8", "replace"))

    def _store(self, message: str) -> None:
        self._maildrop.parent.mkdir(parents=True, exist_ok=True)
        # newline="" keeps the CRLF the message arrived with, so the
        # quoted-printable soft line breaks inside a long link still decode.
        with self._maildrop.open("a", encoding="utf-8", newline="") as sink:
            sink.write(message)
            sink.write(MESSAGE_SEPARATOR)
        print(f"Message written to {self._maildrop}", flush=True)


async def serve(port: int, maildrop: Path) -> None:
    catcher = MailCatcher(maildrop)
    server = await asyncio.start_server(catcher.handle, HOST, port)
    print(f"Catching mail on {HOST}:{port}, writing to {maildrop}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--maildrop", type=Path, default=DEFAULT_MAILDROP)
    arguments = parser.parse_args()
    try:
        asyncio.run(serve(arguments.port, arguments.maildrop))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
