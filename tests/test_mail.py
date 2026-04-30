import pathlib
import site
from email import message_from_string
from unittest.mock import MagicMock

import pytest
import requests
from mailchimp_transactional.api_client import ApiClientError

from mail import MailSendError, send_mail

HERE = pathlib.Path(pathlib.Path(__file__).resolve()).parent
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


def _parse_ses_mime(mock_ses_client):
    """Extract and parse the raw MIME message most-recently sent through the
    mocked SES client.
    """
    call = mock_ses_client.send_raw_email.call_args
    raw = call.kwargs['RawMessage']['Data']
    return message_from_string(raw), call


def test_mandrill_provider_returns_message_id():
    """Default mandrill provider returns a structured result dict.
    """
    result = send_mail(
        'jmilton@example.com',
        ['recipient@example.com'],
        'subj',
        'body',
        )
    assert result['provider'] == 'mandrill'
    assert result['message_id'].startswith('fake-')
    assert result['recipients'][0]['email'] == 'recipient@example.com'
    assert result['recipients'][0]['status'] == 'sent'


def test_ses_provider_returns_message_id(mock_boto3_client):
    """provider='ses' routes through boto3 and returns SES message id.
    """
    result = send_mail(
        'jmilton@example.com',
        ['recipient@example.com'],
        'subj',
        'body',
        provider='ses',
        )
    assert result == {'provider': 'ses',
                      'message_id': 'fake-ses-message-id'}
    call = mock_boto3_client.send_raw_email.call_args
    assert call.kwargs['Source'] == 'jmilton@example.com'
    assert call.kwargs['Destinations'] == ['recipient@example.com']
    parsed = message_from_string(call.kwargs['RawMessage']['Data'])
    assert parsed['Subject'] == 'subj'
    assert parsed.get_payload(decode=True).decode('utf-8') == 'body'


def test_ses_bcc_not_in_mime_headers(mock_boto3_client):
    """BCC addresses must NOT appear in MIME headers (silent leak risk),
    only in the SES Destinations envelope.
    """
    send_mail(
        'jmilton@example.com',
        ['to@example.com'],
        'subj',
        'body',
        bcclist=['secret@example.com'],
        provider='ses',
        )
    parsed, call = _parse_ses_mime(mock_boto3_client)
    assert parsed.get('Bcc') is None
    assert 'secret@example.com' not in call.kwargs['RawMessage']['Data']
    assert 'secret@example.com' in call.kwargs['Destinations']


def test_ses_cc_in_mime_headers(mock_boto3_client):
    """CC addresses appear in both Destinations envelope and the Cc: header.
    """
    send_mail(
        'jmilton@example.com',
        ['to@example.com'],
        'subj',
        'body',
        cclist=['carbon@example.com'],
        provider='ses',
        )
    parsed, call = _parse_ses_mime(mock_boto3_client)
    assert parsed['Cc'] == 'carbon@example.com'
    assert 'carbon@example.com' in call.kwargs['Destinations']


def test_ses_attachment_bytes_round_trip(mock_boto3_client):
    """Attachment payload bytes survive the canonical->MIME translation
    (no double base64 encoding).
    """
    raw_bytes = b'\x89PNG\r\n\x1a\n-fake-pdf-bytes'
    send_mail(
        'jmilton@example.com',
        ['to@example.com'],
        'subj',
        'body',
        attachments=[{'data': raw_bytes,
                      'name': 'doc.pdf',
                      'maintype': 'application',
                      'subtype': 'pdf'}],
        provider='ses',
        )
    parsed, _ = _parse_ses_mime(mock_boto3_client)
    parts = [p for p in parsed.walk()
             if p.get_content_type() == 'application/pdf']
    assert len(parts) == 1
    assert parts[0].get_payload(decode=True) == raw_bytes


def test_ses_inline_image_content_id_preserved(mock_boto3_client):
    """Inline images carry Content-ID matching the cid: reference in body.
    """
    img_bytes = b'\x89PNG\r\n\x1a\n-fake-image-bytes'
    send_mail(
        'jmilton@example.com',
        ['to@example.com'],
        'subj',
        '<html><body><img src="cid:logo123"></body></html>',
        subtype='html',
        inline_images=[{'data': img_bytes,
                        'maintype': 'image',
                        'subtype': 'png',
                        'name': 'logo.png',
                        'cid': 'logo123'}],
        provider='ses',
        )
    parsed, _ = _parse_ses_mime(mock_boto3_client)
    image_parts = [p for p in parsed.walk()
                   if p.get_content_type() == 'image/png']
    assert len(image_parts) == 1
    assert image_parts[0]['Content-ID'] == '<logo123>'
    assert image_parts[0].get_payload(decode=True) == img_bytes


def test_ses_charset_utf8(mock_boto3_client):
    """Non-ASCII subject and body round-trip via utf-8 charset.
    """
    from mail import parse_rfc2047
    send_mail(
        'jmilton@example.com',
        ['to@example.com'],
        'café résumé',
        'naïve façade — €100',
        provider='ses',
        )
    parsed, _ = _parse_ses_mime(mock_boto3_client)
    text_parts = [p for p in parsed.walk()
                  if p.get_content_type() == 'text/plain']
    assert text_parts[0].get_content_charset() == 'utf-8'
    assert parse_rfc2047(parsed['Subject']) == 'café résumé'
    decoded_body = text_parts[0].get_payload(decode=True).decode('utf-8')
    assert decoded_body == 'naïve façade — €100'


def test_subtype_plain_uses_text_field(mock_mandrill_client):
    """subtype='plain' (default) places body in the Mandrill `text` field,
    not `html` (fixes the pre-existing subtype bug).
    """
    mock_mandrill_client.send.return_value = [
        {'_id': 'm1', 'email': 'to@example.com',
         'status': 'sent', 'reject_reason': None},
        ]
    send_mail(
        'jmilton@example.com',
        ['to@example.com'],
        'subj',
        'plain body content',
        )
    call = mock_mandrill_client.send.call_args
    payload = call.args[0]['message']
    assert payload.get('text') == 'plain body content'
    assert 'html' not in payload


def test_subtype_html_uses_html_field(mock_mandrill_client):
    """subtype='html' places body in the Mandrill `html` field.
    """
    mock_mandrill_client.send.return_value = [
        {'_id': 'm1', 'email': 'to@example.com',
         'status': 'sent', 'reject_reason': None},
        ]
    send_mail(
        'jmilton@example.com',
        ['to@example.com'],
        'subj',
        '<p>html body</p>',
        subtype='html',
        )
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    assert payload.get('html') == '<p>html body</p>'
    assert 'text' not in payload


def test_mandrill_sdk_silent_swallow_raises(mock_mandrill_client):
    """When the SDK returns a raw Response (non-2xx with empty body) instead
    of raising, _send_via_mandrill must detect and raise ApiClientError.
    """
    fake_response = MagicMock(spec=requests.Response)
    fake_response.ok = False
    fake_response.text = ''
    fake_response.status_code = 502
    mock_mandrill_client.send.return_value = fake_response
    with pytest.raises(ApiClientError):
        send_mail('jmilton@example.com', ['to@example.com'], 'subj', 'body')


def test_mandrill_all_rejected_raises_mailsenderror(mock_mandrill_client):
    """Every recipient rejected -> MailSendError.
    """
    mock_mandrill_client.send.return_value = [
        {'_id': 'r1', 'email': 'a@example.com',
         'status': 'rejected', 'reject_reason': 'hard-bounce'},
        {'_id': 'r2', 'email': 'b@example.com',
         'status': 'invalid', 'reject_reason': 'bad-domain'},
        ]
    with pytest.raises(MailSendError):
        send_mail(
            'jmilton@example.com',
            ['a@example.com', 'b@example.com'],
            'subj', 'body')


def test_mandrill_partial_rejection_warns_returns_success(
        mock_mandrill_client, caplog):
    """Some recipients rejected -> log warning, return success.
    """
    mock_mandrill_client.send.return_value = [
        {'_id': 'p1', 'email': 'good@example.com',
         'status': 'sent', 'reject_reason': None},
        {'_id': 'p2', 'email': 'bad@example.com',
         'status': 'rejected', 'reject_reason': 'unsub'},
        ]
    import logging
    caplog.set_level(logging.WARNING)
    result = send_mail(
        'jmilton@example.com',
        ['good@example.com', 'bad@example.com'],
        'subj', 'body')
    assert result['provider'] == 'mandrill'
    assert any('rejected 1/2' in r.message for r in caplog.records)


def test_unknown_provider_raises_valueerror():
    """Unrecognized provider -> ValueError.
    """
    with pytest.raises(ValueError, match='Unknown mail provider'):
        send_mail('jmilton@example.com', ['to@example.com'],
                  'subj', 'body', provider='gmail')


def test_ses_without_boto3_raises_importerror(disable_boto3):
    """provider='ses' without boto3 installed -> actionable ImportError.
    """
    with pytest.raises(ImportError, match=r'libb-mail\[ses\]'):
        send_mail('jmilton@example.com', ['to@example.com'],
                  'subj', 'body', provider='ses')


def _stub_mandrill_send(mock_mandrill_client, recipients):
    """Configure the mocked Mandrill SDK to return a synthetic 'sent' result
    for the given recipient addresses.
    """
    mock_mandrill_client.send.return_value = [
        {'_id': f'm-{i}', 'email': r,
         'status': 'sent', 'reject_reason': None}
        for i, r in enumerate(recipients)
        ]


def test_mandrill_cc_in_payload(mock_mandrill_client):
    """Cc addresses appear in Mandrill payload's `to` list with type='cc'.
    """
    _stub_mandrill_send(mock_mandrill_client,
                        ['to@example.com', 'cc@example.com'])
    send_mail('jmilton@example.com', ['to@example.com'],
              'subj', 'body', cclist=['cc@example.com'])
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    by_type = {r['email']: r.get('type') for r in payload['to']}
    assert by_type == {'to@example.com': None, 'cc@example.com': 'cc'}


def test_mandrill_bcc_in_payload(mock_mandrill_client):
    """Bcc addresses appear in Mandrill payload's `to` list with type='bcc'.
    """
    _stub_mandrill_send(mock_mandrill_client,
                        ['to@example.com', 'bcc@example.com'])
    send_mail('jmilton@example.com', ['to@example.com'],
              'subj', 'body', bcclist=['bcc@example.com'])
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    by_type = {r['email']: r.get('type') for r in payload['to']}
    assert by_type == {'to@example.com': None, 'bcc@example.com': 'bcc'}


def test_mandrill_attachment_base64_once(mock_mandrill_client):
    """Mandrill attachment payload contains base64-encoded content (one pass).
    """
    import base64
    raw = b'pdf-bytes-here'
    _stub_mandrill_send(mock_mandrill_client, ['to@example.com'])
    send_mail('jmilton@example.com', ['to@example.com'], 'subj', 'body',
              attachments=[{'data': raw, 'name': 'doc.pdf',
                            'maintype': 'application', 'subtype': 'pdf'}])
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    att = payload['attachments'][0]
    assert att['name'] == 'doc.pdf'
    assert att['type'] == 'application/pdf'
    assert base64.b64decode(att['content']) == raw


def test_mandrill_inline_image_base64_once(mock_mandrill_client):
    """Mandrill inline image goes into payload['images'] with base64 content.
    """
    import base64
    raw = b'png-bytes-here'
    _stub_mandrill_send(mock_mandrill_client, ['to@example.com'])
    send_mail('jmilton@example.com', ['to@example.com'], 'subj',
              '<img src="*|logo|*">', subtype='html',
              inline_images=[{'data': raw, 'maintype': 'image',
                              'subtype': 'png', 'name': 'logo'}])
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    img = payload['images'][0]
    assert img['name'] == 'logo'
    assert img['type'] == 'image/png'
    assert base64.b64decode(img['content']) == raw


def test_mandrill_priority_high_sets_important(mock_mandrill_client):
    """priority='High' sets important=True in the Mandrill payload.
    """
    _stub_mandrill_send(mock_mandrill_client, ['to@example.com'])
    send_mail('jmilton@example.com', ['to@example.com'], 'subj', 'body',
              priority='High')
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    assert payload.get('important') is True


def test_mandrill_priority_normal_omits_important(mock_mandrill_client):
    """Default priority does not set important.
    """
    _stub_mandrill_send(mock_mandrill_client, ['to@example.com'])
    send_mail('jmilton@example.com', ['to@example.com'], 'subj', 'body')
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    assert 'important' not in payload


def test_ses_x_priority_header_high(mock_boto3_client):
    """priority='High' sets X-Priority: 1 on the SES MIME envelope.
    """
    send_mail('jmilton@example.com', ['to@example.com'], 'subj', 'body',
              priority='High', provider='ses')
    parsed, _ = _parse_ses_mime(mock_boto3_client)
    assert parsed['X-Priority'] == '1'


def test_ses_x_priority_header_normal_omitted_or_3(mock_boto3_client):
    """Normal priority maps to X-Priority: 3 (still set, conventional default).
    """
    send_mail('jmilton@example.com', ['to@example.com'], 'subj', 'body',
              provider='ses')
    parsed, _ = _parse_ses_mime(mock_boto3_client)
    assert parsed['X-Priority'] == '3'


def test_ses_attachments_and_inline_images_layout(mock_boto3_client):
    """When both attachments and inline images are present, SES MIME is
    multipart/mixed wrapping multipart/related.
    """
    send_mail(
        'jmilton@example.com', ['to@example.com'], 'subj',
        '<html><body><img src="cid:logo"></body></html>',
        subtype='html',
        attachments=[{'data': b'pdf', 'name': 'a.pdf',
                      'maintype': 'application', 'subtype': 'pdf'}],
        inline_images=[{'data': b'png', 'maintype': 'image',
                        'subtype': 'png', 'name': 'logo'}],
        provider='ses',
        )
    parsed, _ = _parse_ses_mime(mock_boto3_client)
    assert parsed.get_content_type() == 'multipart/mixed'
    top_parts = parsed.get_payload()
    related = [p for p in top_parts
               if p.get_content_type() == 'multipart/related']
    assert len(related) == 1
    sub = related[0].get_payload()
    assert any(p.get_content_type() == 'text/html' for p in sub)
    assert any(p.get_content_type() == 'image/png' for p in sub)
    pdfs = [p for p in top_parts
            if p.get_content_type() == 'application/pdf']
    assert len(pdfs) == 1


def test_three_arg_form_uses_default_sender(mock_mandrill_client):
    """3-arg form (recipients, subject, body) uses config.mail.fromemail.
    """
    _stub_mandrill_send(mock_mandrill_client, ['to@example.com'])
    send_mail(['to@example.com'], 'subj', 'body')
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    from mail import config as mc
    assert payload['from_email'].split('@')[0] == \
        mc.mail.fromemail.split('@')[0]


def test_per_call_provider_overrides_config(mock_boto3_client):
    """Per-call provider= kwarg beats config.mail.provider.

    The conftest fixture pins mail.provider='mandrill'; this call asks for ses
    and must use ses regardless.
    """
    from mail import config as mc
    assert mc.mail.provider == 'mandrill'
    result = send_mail('jmilton@example.com', ['to@example.com'],
                       'subj', 'body', provider='ses')
    assert result['provider'] == 'ses'
    assert mock_boto3_client.send_raw_email.called


def test_ses_boto_error_propagates(mock_boto3_client):
    """Exceptions raised by the boto3 SES client propagate out of send_mail.
    """
    class FakeBotoError(Exception):
        pass

    mock_boto3_client.send_raw_email.side_effect = FakeBotoError(
        'MessageRejected')
    with pytest.raises(FakeBotoError, match='MessageRejected'):
        send_mail('jmilton@example.com', ['to@example.com'],
                  'subj', 'body', provider='ses')


def test_domain_only_rewrites_sender(mock_mandrill_client):
    """domain_only=True rewrites the sender's domain to config.mail.domain.
    """
    _stub_mandrill_send(mock_mandrill_client, ['to@example.com'])
    send_mail('jmilton@external.com', ['to@example.com'], 'subj', 'body')
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    assert payload['from_email'] == 'jmilton@example.com'


def test_domain_only_false_preserves_sender(mock_mandrill_client):
    """domain_only=False keeps the sender's original domain.
    """
    _stub_mandrill_send(mock_mandrill_client, ['to@example.com'])
    send_mail('jmilton@external.com', ['to@example.com'], 'subj', 'body',
              domain_only=False)
    payload = mock_mandrill_client.send.call_args.args[0]['message']
    assert payload['from_email'] == 'jmilton@external.com'


if __name__ == '__main__':
    pytest.main([__file__])
