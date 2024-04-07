import os

from libb import Setting

__all__ = ['mail', 'mandrill']


# Intermedia Email
mail = Setting()
mail.domain = os.getenv('CONFIG_MAIL_DOMAIN')
mail.server = os.getenv('CONFIG_MAIL_SERVER')
mail.fromemail = os.getenv('CONFIG_MAIL_FROMEMAIL')
mail.toemail = os.getenv('CONFIG_MAIL_TOEMAIL')
mail.adminemail = os.getenv('CONFIG_MAIL_ADMINEMAIL')

# Mandrill API integration
mandrill = Setting()
mandrill.apikey = os.getenv('CONFIG_MANDRILL_APIKEY')
mandrill.smtp = os.getenv('CONFIG_MANDRILL_SMTP','smtp.mandrillapp.com')
mandrill.url = os.getenv('CONFIG_MANDRILL_URL', 'https://mandrillapp.com/api/1.0/')


if __name__ == '__main__':
    __import__('doctest').testmod(optionflags=4 | 8 | 32)
