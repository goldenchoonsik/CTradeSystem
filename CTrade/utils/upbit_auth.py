import jwt
import uuid
import hashlib
from urllib.parse import urlencode

class UpbitAuth:
    def __init__(self, access_key, secret_key):
        self.access_key = access_key
        self.secret_key = secret_key

    def get_headers(self, query_dict=None):
        payload = {
            'access_key': self.access_key,
            'nonce': str(uuid.uuid4()),
        }
        if query_dict:
            query_string = urlencode(query_dict).encode()
            m = hashlib.sha512()
            m.update(query_string)
            query_hash = m.hexdigest()
            payload['query_hash'] = query_hash
            payload['query_hash_alg'] = 'SHA512'

        jwt_token = jwt.encode(payload, self.secret_key)
        return {"Authorization": f"Bearer {jwt_token}"}