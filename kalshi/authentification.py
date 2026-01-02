import base64
import datetime
import os
import requests

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from dotenv import load_dotenv, find_dotenv

env_path = find_dotenv()
load_dotenv(env_path)

def load_private_key_from_file(file_path):
    with open(file_path, "rb") as key_file:
        return serialization.load_pem_private_key(
            key_file.read(),
            password=None,
            backend=default_backend()
        )

def load_private_key_from_string(key_string: str):
    key_string = key_string.replace('\\n', '\n')
    return serialization.load_pem_private_key(
        key_string.encode('utf-8'),
        password=None,
        backend=default_backend()
    )

def sign_pss_text(private_key: rsa.RSAPrivateKey, text: str) -> str:
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode("utf-8")

def get_current_timestamp_ms():
    return str(int(datetime.datetime.now().timestamp() * 1000))


def normalize_path(path: str) -> str:
    # MUST NOT include query string in signature
    return path.split("?")[0]

def get_kalshi_api_key():
    kalshi_api_key = os.getenv("kalshi-api-key")
    if kalshi_api_key is None:
        raise ValueError("kalshi-api-key not found in environment variables")
    return kalshi_api_key

def build_msg_string(timestamp_ms: str, method: str, path: str) -> str:
    method = method.upper()
    path = normalize_path(path)
    return timestamp_ms + method + path


def get_authentification_headers(
    method: str,
    path: str,
    private_key_file: str | None = None,
    private_key_string: str | None = None,
):
    timestamp_ms = get_current_timestamp_ms()
    msg_string = build_msg_string(timestamp_ms, method, path)

    if private_key_file:
        kalshi_private_key = load_private_key_from_file(private_key_file)
    elif private_key_string:
        kalshi_private_key = load_private_key_from_string(private_key_string)
    else:
        raise ValueError("private_key_file or private_key_string must be provided")

    signature = sign_pss_text(kalshi_private_key, msg_string)

    return {
        "KALSHI-ACCESS-KEY": get_kalshi_api_key(),
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
    }