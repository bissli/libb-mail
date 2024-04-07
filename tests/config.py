from libb import Setting

Setting.unlock()

# Intermedia Email
mail = Setting()
mail.domain = 'foo.com'
mail.server = 'foobar.com'
mail.fromemail = 'foo@foobar.com'
mail.toemail = 'bar@foobar.com'
mail.adminemail = 'baz@foobar.com'

# Mandrill API integration
mandrill = Setting()
mandrill.apikey = 'mockapi'
mandrill.smtp = 'smtp.mandrillapp.com'
mandrill.url = 'https://mandrillapp.com/api/1.0/'

Setting.lock()

if __name__ == '__main__':
    __import__('doctest').testmod(optionflags=4 | 8 | 32)
