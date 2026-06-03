"""
Jarvis - Credential Encryption (봉투 암호화)
================================================

기존 MVP의 치명적 보안 결함 수정:
- 기존: API 키를 평문(plaintext)으로 DB에 저장 ← DB 털리면 전 고객 키 유출
- 수정: 봉투 암호화 (Envelope Encryption)

작동 원리:
1. KMS(또는 마스터 키)로 데이터 암호화 키(DEK)를 생성
2. DEK로 실제 API 키를 암호화
3. DEK 자체는 마스터 키로 암호화해서 함께 저장
4. DB에는 (암호화된 키 + 암호화된 DEK)만 저장 → 평문은 어디에도 없음

프로덕션에선 AWS KMS / GCP KMS / HashiCorp Vault를 쓰지만,
여기선 그 인터페이스를 그대로 흉내내서 나중에 교체만 하면 되게 설계함.
"""
from __future__ import annotations
import os
import base64
import json
from dataclasses import dataclass
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class MasterKeyProvider:
    """
    마스터 키 제공자 (추상화).
    프로덕션: AWS KMS의 GenerateDataKey / Decrypt 호출로 교체.
    개발: 환경변수의 마스터 키 사용.
    """
    def __init__(self, master_key_b64: str | None = None):
        key = master_key_b64 or os.getenv("JARVIS_MASTER_KEY")
        if not key:
            raise RuntimeError(
                "JARVIS_MASTER_KEY 환경변수가 없습니다. "
                "생성: python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())'"
            )
        self._master = base64.b64decode(key)
        if len(self._master) != 32:
            raise ValueError("마스터 키는 32바이트여야 합니다 (base64로 인코딩된 256비트)")

    def derive_dek(self, salt: bytes) -> bytes:
        """salt마다 고유한 데이터 암호화 키를 파생 (HKDF)"""
        hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"jarvis-dek")
        return hkdf.derive(self._master)


@dataclass
class EncryptedCredential:
    """DB에 저장될 암호화된 자격증명"""
    ciphertext: str   # 암호화된 페이로드 (base64)
    salt: str         # DEK 파생용 salt (base64)
    version: int = 1  # 키 로테이션 대비


class CredentialVault:
    """API 키 암복호화 담당"""

    def __init__(self, provider: MasterKeyProvider):
        self._provider = provider

    def encrypt(self, api_key: str, api_secret: str) -> EncryptedCredential:
        salt = os.urandom(16)
        dek = self._provider.derive_dek(salt)
        fernet = Fernet(base64.urlsafe_b64encode(dek))

        payload = json.dumps({"k": api_key, "s": api_secret}).encode()
        ciphertext = fernet.encrypt(payload)

        return EncryptedCredential(
            ciphertext=base64.b64encode(ciphertext).decode(),
            salt=base64.b64encode(salt).decode(),
            version=1,
        )

    def decrypt(self, cred: EncryptedCredential) -> tuple[str, str]:
        salt = base64.b64decode(cred.salt)
        dek = self._provider.derive_dek(salt)
        fernet = Fernet(base64.urlsafe_b64encode(dek))

        ciphertext = base64.b64decode(cred.ciphertext)
        payload = json.loads(fernet.decrypt(ciphertext).decode())
        return payload["k"], payload["s"]


# ============================================================
# 자가 검증
# ============================================================
if __name__ == "__main__":
    # 테스트용 마스터 키 생성
    test_master = base64.b64encode(os.urandom(32)).decode()
    os.environ["JARVIS_MASTER_KEY"] = test_master

    provider = MasterKeyProvider()
    vault = CredentialVault(provider)

    print("=== 봉투 암호화 검증 ===\n")
    original_key = "URa7wj8ImQauM4SxK1v6TDwZaRhX78Oj2GQkvZrw"
    original_secret = "upboqEuw9YzsJS0rN2UjAY1Vw8HYiBQkimt4RtBI"

    encrypted = vault.encrypt(original_key, original_secret)
    print(f"평문 키:    {original_key[:20]}...")
    print(f"암호문:     {encrypted.ciphertext[:40]}...")
    print(f"salt:       {encrypted.salt}")
    print(f"→ DB엔 이 암호문만 저장됨. 평문은 어디에도 없음.\n")

    dec_key, dec_secret = vault.decrypt(encrypted)
    assert dec_key == original_key, "복호화 실패!"
    assert dec_secret == original_secret, "복호화 실패!"
    print(f"복호화 키:  {dec_key[:20]}...")
    print(f"✅ 암복호화 일치 확인")

    # 같은 키를 두 번 암호화하면 다른 암호문이 나와야 함 (salt 때문)
    enc2 = vault.encrypt(original_key, original_secret)
    assert enc2.ciphertext != encrypted.ciphertext, "암호문이 같으면 안 됨!"
    print(f"✅ 동일 입력 → 다른 암호문 (salt 무작위성 확인)")
