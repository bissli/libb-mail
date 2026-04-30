import base64
import contextlib
import datetime
import json
import logging
import os
import re
from email.header import decode_header
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import mailchimp_transactional as MailchimpTransactional
import requests
from mailchimp_transactional.api_client import ApiClientError

try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError
except ImportError:
    boto3 = None
    BotoClientError = None

import pathlib

from libb import guess_type
from mail import config

__all__ = [
    'send_mail',
    'get_mail_status',
    'parse_rfc2047',
    'MailClient',
    'MailSendError',
    'GENERAL_TYPES',
    'EXCEL_TYPES',
    'PDF_TYPES',
    'ALLOWED_ATTACHMENT_TYPES',
    'ALLOWED_MIME_TYPES',
    ]

logger = logging.getLogger(__name__)


GENERAL_TYPES = {
    'application/octetstream': None,
    'application/octet-stream': None,
}
EXCEL_TYPES = {
    'application/vnd.ms-excel': 'xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
}
PDF_TYPES = {
    'application/pdf': 'pdf',
    'application/x-pdf': 'pdf',
}
ALLOWED_ATTACHMENT_TYPES = dict(list(GENERAL_TYPES.items()) + list(EXCEL_TYPES.items()) + list(PDF_TYPES.items()))
ALLOWED_MIME_TYPES = {v: k for k, v in ALLOWED_ATTACHMENT_TYPES.items()}


def parse_rfc2047(rfc2047text):
    """Decode sytax specified in [RFC-2047](https://tools.ietf.org/html/rfc2047)
    from [blog](https://dmorgan.info/posts/encoded-word-syntax/)
    -- basic decoding format: =?<charset>?<encoding>?<encoded-text>?=

    >>> parse_rfc2047('=?UTF-8?B?VGhpcyBpcyBhIGhvcnNleTog8J+Qjg==?=')
    'This is a horsey: 🐎'
    >>> parse_rfc2047('=?UTF-8?B?KEJOKSBXYWxsIFN0cmVldCBTZWFyY2hpbmcgZm9yIENsdWVzIEJlaGluZCB0aGUgVklY4g==?=     =?UTF-8?B?gJlzIFZlcnkgV2VpcmQ=?=')
    '(BN) Wall Street Searching for Clues Behind the VIX’s Very Weird'
    >>> parse_rfc2047('Already a string!')
    'Already a string!'
    """

    messages = decode_header(rfc2047text)
    if not messages:
        logger.error(f'Unable to parse {str(rfc2047text)}')
        return
    output = ''
    for content, encoding in messages:
        if not content:
            continue
        if isinstance(content, str):
            output += content
        else:
            with contextlib.suppress(Exception):
                output += content.decode(encoding or 'utf-8')
    return output or None


class MailClient:
    """Base class for email contexts"""

    def __new__(cls, *args, **kwargs):
        if cls is MailClient:
            raise TypeError('Base class may not be instantiated')
        return object.__new__(cls)

    def get_emails(self, *args, **kwargs):
        """Email generator, given an imap connection object and search kwargs
        search kwargs definied in [RFC3501 p50](https://tools.ietf.org/html/rfc3501#page-50)
        email headers can also be search - see [RFC 2822](https://tools.ietf.org/html/rfc2822)
        """
        raise NotImplementedError('Must be implemented')

    def send_mail(self, *args, **kwargs):
        raise NotImplementedError('Must be implemented')

    def get_attachment(self, mail, types=ALLOWED_ATTACHMENT_TYPES):
        """Given an email.Message object, walk the parts looking for attachments"""
        for part in mail.walk():
            content_disposition = part.get('Content-Disposition')
            content_maintype = part.get_content_maintype()

            if content_maintype == 'multipart' or content_disposition is None:
                logger.debug('Skipping multipart or missing content-disposition')
                continue
            filename = part.get_filename()
            content_type = part.get_content_type()

            if content_type not in types:
                logger.warning(f'Skipping file with unallowed content_type: {content_type}')
                continue

            ext = types[content_type]
            if ext is None:
                _, ext = os.path.splitext(filename)
                ext = ext.strip('.').lower()
                if ext not in list(types.values()):
                    logger.warning(f'Extension {ext} was not in list of types, skipping')
                    continue

            yield part.get_payload(decode=True), filename, ext

    def parse_sent_time(self, sent_time):
        """Parse the date from an imap mail object into a python datetime object
        email servers should all assume UTC
        """
        try:
            sent_time = sent_time.strip()
        except:
            sent_time = datetime.datetime.now()
        assert sent_time, 'Every email should have a sent date/time'
        if ',' in sent_time:
            pattern = '%a, %d %b %Y %H:%M:%S'
            exp_len = 5
        else:
            pattern = '%d %b %Y %H:%M:%S'
            exp_len = 4
        _split = sent_time.split(' ')
        if len(_split) > exp_len:
            sent_time = ' '.join(_split[:exp_len])
            logger.warning(f'Stripped time with nonstandard timezone: {sent_time}')
        try:
            parsed_sent_time = datetime.datetime.strptime(sent_time, pattern)
        except ValueError:
            parsed_sent_time = sent_time
        return parsed_sent_time

    def parse_email_addresses(self, addr):
        """Parse email addresses out of exchange addresss which includes other info
        we don't care about, such as first and last names
        """
        if not addr:
            return None
        parsed_addrs = ','.join(re.findall('<.*?>', addr))
        parsed_addrs = ''.join(c for c in parsed_addrs if c not in {'<', '>'}).lower()
        return parsed_addrs

    def parse_body(self, mail, decode=False, prefer_text=True):
        """Get our main body type from an `email.Message`, potentially multipart"""
        if mail.is_multipart():
            body = ''
            for part in mail.walk():
                content_disposition = str(part.get('Content-Disposition', ''))
                content_type = part.get_content_type()
                if content_type == 'multipart/alternative':
                    for _part in part.get_payload()[1:]:
                        body += self._flatten_payload(_part, decode)
                    break
                if content_type == 'text/plain' and 'attachment' not in content_disposition:
                    body += part.get_payload(decode=decode)
            if decode:
                body = str(body, errors='ignore')
            return body

        text = html = None
        for i, part in enumerate(mail.walk()):
            content_type = part.get_content_type()
            if content_type == 'text/html':
                if not html:
                    html = part.get_payload(decode=decode)
                    if decode:
                        html = str(html, errors='ignore')
                    logger.debug(f'Calling first html part the body {i}')
                else:
                    logger.debug(f'Skipping extra html part {i}')
            elif content_type.startswith('text/'):
                if not text:
                    text = part.get_payload(decode=decode)
                    if decode:
                        text = str(text, errors='ignore')
                    logger.debug(f'Calling first text part the body text version {i}')
                else:
                    logger.debug(f'Skipping extra text part {i}')
            else:
                logger.debug(f'Skipping non-text content type {i}, {content_type}')

        if prefer_text:
            logger.info('Returning text payload')
            return text or html

        logger.info('Returning html payload')
        return html or text

    def parse_attachment_filenames(self, email):
        """Parse just the filenames of any attachements by walking through email"""
        filenames = [part.get_filename() for part in email.walk() if part.get_filename() is not None]
        return '; '.join(filenames) if filenames else None

    def parse_email(self, email, decode=False):
        """Takes an imap mail object and parses each section accordingly and returns
        all sections in an attrdict
        """
        from libb import attrdict
        parsed_email = attrdict()
        if not email['Date']:
            return
        parsed_email.sent_time = self.parse_sent_time(email['Date'])
        parsed_email.email_from = self.parse_email_addresses(email['From'])
        parsed_email.email_to = self.parse_email_addresses(email['To'])
        parsed_email.email_cc = self.parse_email_addresses(email['CC'])
        parsed_email.email_bcc = self.parse_email_addresses(email['BCC'])
        parsed_email.subject = parse_rfc2047(email['Subject'])
        parsed_email.body = self.parse_body(email, decode)
        parsed_email.attachments = self.parse_attachment_filenames(email)
        parsed_email.flags = email['Keywords'].split(',') if email['Keywords'] else []
        return parsed_email

    def _flatten_payload(self, payload, decode=False):
        """Recrusively flatten email.Message objects"""
        msg = ''
        if isinstance(payload, str):
            msg += payload
        elif isinstance(payload, list):
            for item in payload:
                msg += self._flatten_payload(item, decode)
        elif payload:
            payload = payload.get_payload(decode=decode)
            msg += self._flatten_payload(payload, decode)
        return msg


#
# Mail sending
#


class MailSendError(Exception):
    """Raised when the upstream provider rejected every recipient.
    """


_PRIORITY_X_HEADER = {'High': '1', 'Normal': '3', 'Low': '5'}


def _resolve_recipients(addrs):
    """Append @{config.mail.domain} to bare usernames in addrs.
    """
    if not addrs:
        return addrs
    out = []
    for r in addrs:
        if '@' not in r:
            r = f'{r}@{config.mail.domain}'
        out.append(r)
    return out


def _canonicalize_attachment(attachment):
    """Normalize an attachment input to canonical form with raw bytes content.

    Accepts a path string or a dict with keys (path | data, name, maintype, subtype).
    """
    if isinstance(attachment, dict):
        path = attachment.get('path')
        data = attachment.get('data')
        name = attachment.get('name')
        maintype = attachment.get('maintype', 'application')
        subtype = attachment.get('subtype', 'octet-stream')
    else:
        path = attachment
        data = None
        name = None
        maintype = 'application'
        subtype = 'octet-stream'
    if path:
        ctype = guess_type(path)
        if ctype is not None:
            maintype, subtype = ctype.split('/', 1)
        content = pathlib.Path(path).read_bytes()
        name = name or os.path.split(path)[-1]
    else:
        content = data
    return {'content': content,
            'mime_type': f'{maintype}/{subtype}',
            'name': name}


def _canonicalize_inline_image(image):
    """Normalize an inline image input to canonical form with raw bytes content.

    `cid` defaults to `name` when not provided.
    """
    if not isinstance(image, dict):
        raise TypeError(
            f'Inline image must be a dict, got {type(image).__name__}')
    return {
        'content': image['data'],
        'mime_type': f"{image['maintype']}/{image['subtype']}",
        'name': image['name'],
        'cid': image.get('cid', image['name']),
        }


def _build_canonical_message(*args, **kwargs):
    """Parse send_mail args/kwargs into a backend-agnostic message dict.
    """
    if len(args) == 4:
        sender, recipients, subject, body = args
    elif len(args) == 3:
        sender = config.mail.fromemail
        recipients, subject, body = args
    else:
        raise TypeError(
            f'send_mail expects 3 or 4 positional args, got {len(args)}')

    if not isinstance(recipients, (tuple, list)):
        logger.warning(
            f'Recipients should be a list or tuple: wrapping {type(recipients)}')
        recipients = [recipients]

    priority = kwargs.get('priority', 'Normal')
    subtype = kwargs.get('subtype', 'plain')
    cclist = list(kwargs.get('cclist') or [])
    bcclist = list(kwargs.get('bcclist') or [])
    attachments_in = kwargs.get('attachments') or []
    inline_images_in = kwargs.get('inline_images') or []
    domain_only = kwargs.get('domain_only', True)

    recipients = _resolve_recipients(list(recipients))
    cclist = _resolve_recipients(cclist)
    bcclist = _resolve_recipients(bcclist)

    if domain_only:
        sender = sender.split('@')[0] + f'@{config.mail.domain}'

    return {
        'sender': sender,
        'recipients': recipients,
        'cc': cclist,
        'bcc': bcclist,
        'subject': subject,
        'body': body,
        'subtype': subtype,
        'priority': priority,
        'attachments': [_canonicalize_attachment(a) for a in attachments_in],
        'inline_images': [_canonicalize_inline_image(i) for i in inline_images_in],
        }


def _send_via_mandrill(msg):
    """Send a canonical message through Mandrill. Returns a result dict.
    """
    payload = {
        'from_email': msg['sender'],
        'to': [{'email': e} for e in msg['recipients']],
        'subject': msg['subject'],
        }
    body_field = 'html' if msg['subtype'] == 'html' else 'text'
    payload[body_field] = msg['body']

    if msg['attachments']:
        payload['attachments'] = [
            {'type': a['mime_type'],
             'name': a['name'],
             'content': base64.b64encode(a['content']).decode('ascii')}
            for a in msg['attachments']
            ]
    if msg['inline_images']:
        payload['images'] = [
            {'type': i['mime_type'],
             'name': i['name'],
             'content': base64.b64encode(i['content']).decode('ascii')}
            for i in msg['inline_images']
            ]
    if msg['priority'] != 'Normal':
        payload['important'] = True
    if msg['cc']:
        payload['to'].extend([{'email': e, 'type': 'cc'} for e in msg['cc']])
        logger.info(f"CC'ing {';'.join(msg['cc'])}")
    if msg['bcc']:
        payload['to'].extend([{'email': e, 'type': 'bcc'} for e in msg['bcc']])
        logger.info(f"BCC'ing {';'.join(msg['bcc'])}")

    server = MailchimpTransactional.Client(config.mandrill.apikey)
    result = server.messages.send({'message': payload})

    if isinstance(result, requests.Response):
        if not result.ok:
            raise ApiClientError(text=result.text,
                                 status_code=result.status_code)
        result = result.json() if result.text else []

    if not isinstance(result, list):
        raise MailSendError(
            f'Unexpected Mandrill response shape: {type(result).__name__}')

    rejected = [r for r in result
                if r.get('status') in {'rejected', 'invalid'}]
    if result and len(rejected) == len(result):
        reasons = ', '.join(
            f"{r.get('email')}: {r.get('reject_reason')}" for r in rejected)
        raise MailSendError(f'All recipients rejected by Mandrill: {reasons}')
    if rejected:
        logger.warning(
            f'Mandrill rejected {len(rejected)}/{len(result)} recipients: '
            + ', '.join(
                f"{r.get('email')}: {r.get('reject_reason')}" for r in rejected))

    toaddrs = msg['recipients'] + msg['cc'] + msg['bcc']
    logger.info(f"Sent email via Mandrill from {msg['sender']} to {toaddrs}")
    message_id = result[0].get('_id') if result else None
    return {
        'provider': 'mandrill',
        'message_id': message_id,
        'recipients': [
            {'email': r.get('email'),
             'status': r.get('status'),
             'reject_reason': r.get('reject_reason')}
            for r in result
            ],
        }


def _build_ses_mime(msg):
    """Build a MIME envelope for SES send_raw_email from a canonical message.

    BCC addresses are intentionally NOT added as MIME headers; SES forwards
    the raw bytes verbatim and a Bcc: header would leak BCC recipients to
    every other recipient. BCC delivery happens via the Destinations envelope.
    """
    body_part = MIMEText(msg['body'],
                         _subtype=msg['subtype'],
                         _charset='utf-8')

    if msg['inline_images']:
        related = MIMEMultipart('related')
        related.attach(body_part)
        for img in msg['inline_images']:
            subtype = img['mime_type'].split('/', 1)[1]
            mime_img = MIMEImage(img['content'], _subtype=subtype)
            mime_img.add_header('Content-ID', f"<{img['cid']}>")
            mime_img.add_header(
                'Content-Disposition', 'inline', filename=img['name'])
            related.attach(mime_img)
        inner = related
    else:
        inner = body_part

    if msg['attachments']:
        outer = MIMEMultipart('mixed')
        outer.attach(inner)
        for att in msg['attachments']:
            maintype, subtype = att['mime_type'].split('/', 1)
            sub_for_app = subtype if maintype == 'application' else 'octet-stream'
            part = MIMEApplication(att['content'], _subtype=sub_for_app)
            part.add_header(
                'Content-Disposition', 'attachment', filename=att['name'])
            outer.attach(part)
        envelope = outer
    else:
        envelope = inner

    envelope['From'] = msg['sender']
    envelope['To'] = ', '.join(msg['recipients'])
    if msg['cc']:
        envelope['Cc'] = ', '.join(msg['cc'])
    envelope['Subject'] = msg['subject']
    if msg['priority'] in _PRIORITY_X_HEADER:
        envelope['X-Priority'] = _PRIORITY_X_HEADER[msg['priority']]
    return envelope


def _send_via_ses(msg):
    """Send a canonical message through AWS SES. Returns a result dict.
    """
    if boto3 is None:
        raise ImportError(
            "SES backend requires the 'ses' extra: "
            'pip install libb-mail[ses]')

    mime = _build_ses_mime(msg)
    destinations = msg['recipients'] + msg['cc'] + msg['bcc']

    client_kwargs = {'region_name': config.ses.region}
    if config.ses.access_key_id:
        client_kwargs['aws_access_key_id'] = config.ses.access_key_id
    if config.ses.secret_access_key:
        client_kwargs['aws_secret_access_key'] = config.ses.secret_access_key
    client = boto3.client('ses', **client_kwargs)

    response = client.send_raw_email(
        Source=msg['sender'],
        Destinations=destinations,
        RawMessage={'Data': mime.as_string()},
        )
    logger.info(f"Sent email via SES from {msg['sender']} to {destinations}")
    return {'provider': 'ses', 'message_id': response.get('MessageId')}


def send_mail(*args, **kwargs):
    """Send mail via the selected backend (Mandrill or SES).

    Positional args (3 or 4):
      - 3-arg form: (recipients, subject, body); sender defaults to
        config.mail.fromemail.
      - 4-arg form: (sender, recipients, subject, body).

    Keyword args:
      - priority: 'High' | 'Normal' | 'Low' (default 'Normal')
      - subtype: 'plain' | 'html' (default 'plain')
      - cclist, bcclist: cc / bcc address lists
      - attachments: list of paths or dicts (path | data, name, maintype, subtype)
      - inline_images: list of dicts (data, maintype, subtype, name, optional cid)
      - domain_only: rewrite sender to the configured domain (default True)
      - provider: 'mandrill' | 'ses' (overrides config.mail.provider)

    Returns {'provider', 'message_id', ...}. Raises on send failure.
    """
    msg = _build_canonical_message(*args, **kwargs)
    provider = kwargs.get('provider') or config.mail.provider
    if provider == 'mandrill':
        return _send_via_mandrill(msg)
    if provider == 'ses':
        return _send_via_ses(msg)
    raise ValueError(
        f'Unknown mail provider: {provider!r} (expected mandrill or ses)')


def create_multipart(body):
    """Create a multipart email message
    according to [RFC 2046 p.24](https://www.ietf.org/rfc/rfc2046.txt)
    last attachment is 'best and preferred'
    """
    eml = MIMEMultipart('alternative')
    eml.attach(MIMEText(body, 'plain', 'utf-8'))
    eml.attach(MIMEText(body, 'html', 'utf-8'))
    return eml


def create_attachment(path=None, data=None, name=None, maintype='application', subtype='octet-stream'):
    """Mandrill requires an array attachment
    type==string: the MIME type of the attachment
    name==string: the file name of the attachment
    content==string: the content encoded as a base64-encoded string
    """
    if path:
        ctype = guess_type(path)
        if ctype is not None:
            maintype, subtype = ctype.split('/', 1)
        with pathlib.Path(path).open('rb') as fp:
            content = base64.b64encode(fp.read()).decode('ascii')
        name = name or os.path.split(path)[-1]
    else:
        content = base64.b64encode(data).decode('ascii')
    return [{'content': content, 'type': maintype + '/' + subtype, 'name': name}]


def call_mandrill_api(endpoint, data):
    """Query Mandrill's API by POSTing JSON `data` to a given endpoint.

    FULL API Docs: https://mandrillapp.com/api/docs/
    """
    url = f'{config.mandrill.url}/{endpoint}'
    r = requests.post(url, data=json.dumps(data))
    return json.loads(r.text)


def get_mail_status(email_from=None, date_from=None, date_to=None, limit=1000, query=''):
    """Get mail status by calling Mandrill's SEARCH endpoint.

    'Mandrill searches utilize Lucene queries'.
    https://mailchimp.zendesk.com/hc/en-us/articles/205583137-How-do-I-search-my-outbound-activity-in-mailchimp-

    RETURNS a list of (email_address, delivery status, timesent)
    """
    endpoint = 'messages/search.json'
    data = {
        'key': config.mandrill.apikey,
        'query': query,
        'senders': email_from and [email_from],
        'date_from': date_from and f'{date_from:%Y-%m-%d}',
        'date_to': date_to and f'{date_to:%Y-%m-%d}',
        'limit': limit,
    }
    data = {k: v for (k, v) in data.items() if v}

    msgs = call_mandrill_api(endpoint, data)
    msgs = [{'email': m['email'], 'status': m['state'], 'timesent': m['ts']} for m in msgs]
    logger.info(f'retrieved {len(msgs)} messages from Mandrill')
    return msgs


if __name__ == '__main__':
    __import__('doctest').testmod(optionflags=4 | 8 | 32)
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
