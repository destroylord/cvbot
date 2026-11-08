"""Kirim email lewat Gmail (SMTP + App Password)."""
import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from gmail_accounts import Account


def send_application(account: Account, to: str, subject: str, body: str, attachment_path: str | None = None,
                     in_reply_to: str | None = None) -> str:
    """Kirim email. Mengembalikan Message-ID supaya follow-up bisa masuk ke thread yang sama."""
    sender = account.address

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    message_id = make_msgid(domain=sender.split("@")[-1])
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    if attachment_path and Path(attachment_path).exists():
        ctype, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(Path(attachment_path).read_bytes(), maintype=maintype, subtype=subtype,
                           filename=Path(attachment_path).name)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, account.password)
        smtp.send_message(msg)
    return message_id
