import os
import site

import pytest

from mail import send_mail

HERE = os.path.dirname(os.path.abspath(__file__))
site.addsitedir(HERE)
import config


def test_send():
    send_mail(
        config.mail.fromemail,
        [config.mail.adminemail],
        'Test message',
        """
    This is a test email message
    from the mail.py Python module.

    Does it work?
        """,
        priority='High',
        bcclist=[config.mail.toemail],
    )

    send_mail(
        config.mail.fromemail,
        [config.mail.adminemail],
        'Test message',
        """
    <html><body>
    <p>
    This is a test email message
    from the mail.py Python module.

    Does it work?
    </p>
    <pre>
    def foo():
        #Sample function
        pass
    </pre>
    </body>
    </html>
        """,
        priority='High',
        subtype='html',
        cclist=[config.mail.toemail],
    )


if __name__ == '__main__':
    pytest.main([__file__])
