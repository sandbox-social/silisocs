from mastodon import Mastodon

MASTODON_CLIENT_ID = "NdIWDUSwPJv1CA3GsJHps3lvOJ2uqChOcxnq0UI8yiE"
MASTODON_CLIENT_SECRET = "b6eOuHMGkGZ76gkC52vocYIiswmTFRi0Cws9sCkAXts"

mastodon = Mastodon(access_token="your_token_here", api_base_url="https://social-sandbox.com")
print(mastodon.account_verify_credentials())
