"""Consumer mailbox domains shared by unrelated people: an account on one of these is matched by its
exact address only, never by domain. (Data, not connectors: excluded from the provider-name test.)"""

FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "outlook.com", "hotmail.com",
    "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.de", "mail.com", "yandex.com", "zoho.com", "fastmail.com", "hey.com",
})
