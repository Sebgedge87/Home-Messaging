from py_vapid import Vapid01

keys = Vapid01().generate_keys()
print('VAPID_PUBLIC_KEY=', keys.public_key)
print('VAPID_PRIVATE_KEY=', keys.private_key)
