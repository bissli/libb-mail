import os

from libb import Setting

__all__ = ['mail', 'mandrill', 'ses']

Setting.unlock()

# Intermedia Email
mail = Setting()
mail.domain = os.getenv('CONFIG_MAIL_DOMAIN')
mail.server = os.getenv('CONFIG_MAIL_SERVER')
mail.fromemail = os.getenv('CONFIG_MAIL_FROMEMAIL')
mail.toemail = os.getenv('CONFIG_MAIL_TOEMAIL')
mail.adminemail = os.getenv('CONFIG_MAIL_ADMINEMAIL')
mail.provider = os.getenv('CONFIG_MAIL_PROVIDER', 'mandrill')

# Mandrill API integration
mandrill = Setting()
mandrill.apikey = os.getenv('CONFIG_MANDRILL_APIKEY')
mandrill.smtp = os.getenv('CONFIG_MANDRILL_SMTP','smtp.mandrillapp.com')
mandrill.url = os.getenv('CONFIG_MANDRILL_URL', 'https://mandrillapp.com/api/1.0/')

# AWS SES integration (optional backend; install with: pip install libb-mail[ses])
ses = Setting()
ses.region = os.getenv('CONFIG_SES_REGION', 'us-east-1')
ses.access_key_id = os.getenv('CONFIG_SES_ACCESS_KEY_ID')
ses.secret_access_key = os.getenv('CONFIG_SES_SECRET_ACCESS_KEY')

Setting.lock()

if __name__ == '__main__':
    __import__('doctest').testmod(optionflags=4 | 8 | 32)
