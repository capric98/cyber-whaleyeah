import logging
import mimetypes
from dataclasses import dataclass
from typing import Literal, Set
import httpx
from telegram import Message
from humanfriendly import format_size
from whaleyeah.rich_message import extract_message_text


logger = logging.getLogger(__name__)


@dataclass
class ExtractedAttachment:
    type: Literal["photo", "audio", "document"]
    mime_type: str
    file_name: str
    bytes: bytes


@dataclass
class ExtractedMessage:
    text: str
    attachments: list[ExtractedAttachment]


class AttachmentTooLargeError(Exception):
    pass


class AttachmentDownloadError(Exception):
    pass


async def get_url_bytes(url: str, timeout: float = 10.0) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True, timeout=timeout)
        response.raise_for_status()
        return response.content


def strip_bot_command(text: str, command: str, bot_name: str) -> str:
    return text.removeprefix(f"/{command}").removeprefix(bot_name).strip()


async def extract_media(
    message: Message,
    max_attach_size: int,
    allowed_types: Set[str],
    is_command: bool = False,
    command: str = "",
) -> ExtractedMessage:
    """
    Extracts text and specified attachments from a Telegram message.
    Raises AttachmentTooLargeError or AttachmentDownloadError on failure.
    """
    if is_command:
        source = message.caption or message.text
        if source:
            text = strip_bot_command(source, command, message.get_bot().name)
        else:
            text = ""
    else:
        text = extract_message_text(message)

    attachments = []

    # 1. Photo
    if "photo" in allowed_types and message.photo:
        photos = list(message.photo)
        photos.sort(key=lambda v: v.width, reverse=True)
        photo = photos[0]

        file_size = getattr(photo, "file_size", None)
        if file_size and file_size > max_attach_size:
            raise AttachmentTooLargeError(f"附件超出 {format_size(max_attach_size, binary=True)} 大小限制！")

        file_bytes: bytes | None = None
        try:
            tg_file = await photo.get_file()

            if tg_file.file_path:
                file_bytes = await get_url_bytes(tg_file.file_path)
        except Exception as e:
            raise AttachmentDownloadError(str(e))

        if file_bytes:
            if len(file_bytes) > max_attach_size:
                raise AttachmentTooLargeError(f"附件超出 {format_size(max_attach_size, binary=True)} 大小限制！")

            file_name = "photo.jpg"
            mime_type = "image/jpeg"
            file_path = tg_file.file_path

            if file_path:
                file_name = file_path.split("/")[-1]
                guessed_mime = mimetypes.guess_type(file_path)[0]
                if guessed_mime:
                    mime_type = guessed_mime
                else:
                    if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                        mime_type = "image/png"
                    elif file_bytes.startswith(b"\xff\xd8\xff"):
                        mime_type = "image/jpeg"
                    elif file_bytes.startswith(b"RIFF") and b"WEBP" in file_bytes[8:16]:
                        mime_type = "image/webp"
                    elif file_bytes.startswith(b"GIF8"):
                        mime_type = "image/gif"

            attachments.append(ExtractedAttachment(
                type="photo",
                mime_type=mime_type,
                file_name=file_name,
                bytes=file_bytes
            ))

    # 2. Audio & Document
    for atype in ["audio", "document"]:
        if atype in allowed_types:
            attachment = getattr(message, atype, None)
            if attachment:
                file_size = getattr(attachment, "file_size", None)
                if file_size and file_size > max_attach_size:
                    raise AttachmentTooLargeError(f"附件超出 {format_size(max_attach_size, binary=True)} 大小限制！")

                file_name = getattr(attachment, "file_name", f"{atype}")
                mime_type = getattr(attachment, "mime_type", None) or mimetypes.guess_type(file_name)[0] or "application/octet-stream"

                try:
                    tg_file = await attachment.get_file()
                    file_bytes = await get_url_bytes(tg_file.file_path)
                except Exception as e:
                    raise AttachmentDownloadError(str(e))

                if len(file_bytes) > max_attach_size:
                    raise AttachmentTooLargeError(f"附件超出 {format_size(max_attach_size, binary=True)} 大小限制！")

                attachments.append(ExtractedAttachment(
                    type=atype,  # type: ignore
                    mime_type=mime_type,
                    file_name=file_name,
                    bytes=file_bytes
                ))

    return ExtractedMessage(text=text, attachments=attachments)
