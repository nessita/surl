#!/usr/bin/env python3
"""
S(tore)URL ....

Authorising the client::

  $ surl -e foo@bar.com -s production surl -a foo-prod

  OR

  $ STORE_EMAIL=foo@bar.com STORE_ENV=production surl -a foo-prod
  Password for foo@bar.com: *****
  2FA (if enabled):
  ...

That will record authorization information in `$SNAP_USER_COMMON` if
it's a snap or local diretory if run from source.
(CAREFULL, IT WILL BE IN PLAINTEXT)

Use '-p package_access -p package_upload' switch to create more capable
authorizations.

Then use it for performing actions on the Store API (defaults to
ACL verification)::

  $ ./surl.py -a foo-prod | jq .
  {
    "account": {
      "openid": "*****",
      "verified": true,
      "displayname": "Foo Bar",
      "email": "foo@bar.com"
    },
    "last_auth": "2016-08-11T01:23:02.627614",
    "refresh_required": false,
    "allowed": true,
    "permissions": [
      "package_access"
    ]
  }

Registering a new snap name::

  $ ./surl.py -a foo-prod -d'{"snap_name": "surl"}' \
    https://dashboard.snapcraft.io/dev/api/register-name/ | jq .
  {
    "snap_id": "LpV8761EjlAPqeXxfYhQvpSWgpxvEWpN"
  }

"""

import argparse
import datetime
import getpass
import json
import os
import sys

import requests
from pymacaroons import Macaroon


DEFAULT_HEADERS = {
    'User-Agent': 'surl/{}'.format(os.environ.get('SNAP_VERSION', 'devel')),
    'Accept': 'application/json, application/hal+json',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache',
}

CONSTANTS = {
    'local': {
        'sso_location': os.environ.get(
            'SURL_SSO_LOCATION',
            'login.staging.ubuntu.com'),
        'sso_base_url': os.environ.get(
            'SURL_SSO_BASE_URL',
            'https://login.staging.ubuntu.com'),
        'sca_base_url': os.environ.get(
            'SURL_SCA_BASE_URL',
            'http://0.0.0.0:8000'),
    },
    'staging': {
        'sso_location': 'login.staging.ubuntu.com',
        'sso_base_url': 'https://login.staging.ubuntu.com',
        'sca_base_url': os.environ.get('SURL_SCA_ROOT_URL',
                                       'https://dashboard.staging.snapcraft.io'),
    },
    'production': {
        'sso_location': 'login.ubuntu.com',
        'sso_base_url': 'https://login.ubuntu.com',
        'sca_base_url': 'https://dashboard.snapcraft.io',
    },
}


def get_store_authorization(email, permissions=None, channels=None, store_env=None):
    """Return the serialised root and discharge macaroon.

    Get a permissions macaroon from SCA and discharge it in SSO.
    """
    headers = DEFAULT_HEADERS.copy()
    # Request a SCA root macaroon with hard expiration in 180 days.
    sca_data = {
        'permissions': permissions or ['package_access'],
        'expires': (
            datetime.date.today() + datetime.timedelta(days=180)
            ).strftime('%Y-%m-%d 00:00:00')
    }
    if channels:
        sca_data.update({
            'channels': channels
        })
    response = requests.request(
        url='{}/dev/api/acl/'.format(CONSTANTS[store_env]['sca_base_url']),
        method='POST', json=sca_data, headers=headers)
    root = response.json()['macaroon']

    caveat, = [
        c for c in Macaroon.deserialize(root).third_party_caveats()
        if c.location == CONSTANTS[store_env]['sso_location']
    ]
    # Request a SSO discharge macaroon.
    sso_data = {
        'email': email,
        'password': getpass.getpass('Password for {}: '.format(email)),
        'caveat_id': caveat.caveat_id,
    }
    response = requests.request(
        url='{}/api/v2/tokens/discharge'.format(CONSTANTS[store_env]['sso_base_url']),
        method='POST', json=sso_data, headers=headers)
    # OTP/2FA is optional.
    if (response.status_code == 401 and
            response.json().get('code') == 'TWOFACTOR_REQUIRED'):
        sys.stderr.write('Second-factor auth: ')
        sso_data.update({'otp': input()})
        response = requests.request(
            url='{}/api/v2/tokens/discharge'.format(CONSTANTS[store_env]['sso_base_url']),
            method='POST', json=sso_data, headers=headers)
    discharge = response.json()['discharge_macaroon']

    return root, discharge

def get_authorization_header(root, discharge):
    """Bind root and discharge macaroons and return the authorization header."""
    bound = Macaroon.deserialize(root).prepare_for_request(
        Macaroon.deserialize(discharge))

    return 'Macaroon root={}, discharge={}'.format(root, bound.serialize())


def get_refreshed_discharge(discharge, store_env):
    headers = DEFAULT_HEADERS.copy()
    data = {'discharge_macaroon': discharge}
    response = requests.request(
        url='{}/api/v2/tokens/refresh'.format(CONSTANTS[store_env]['sso_base_url']),
        method='POST', json=data, headers=headers)
    return response.json()['discharge_macaroon']


def main():
    parser = argparse.ArgumentParser(
        description='S(tore)URL ...'
    )
    parser.add_argument(
        '--version', action='version',
        version='surl "{}"'.format(os.environ.get('SNAP_VERSION', 'devel')))
    parser.add_argument('-v', '--debug', action='store_true',
                        help='Prints request and response headers')
    parser.add_argument('--pretty', action='store_true',
                        help='Prints pretty json (sorted keys and 4-spaces for indent)')

    # Credential options.
    parser.add_argument(
        '-a', '--auth', metavar='IDENT',
        help='Authorization identifier (saving or reading).')
    parser.add_argument(
        '--force', action='store_true',
        help='Force re-authorization and overrides saved information.')
    parser.add_argument(
        '-l', '--list-auth', action='store_true',
        help='List stored authorizations..')
    parser.add_argument(
        '-e', '--email', default=os.environ.get('STORE_EMAIL'))
    parser.add_argument(
        '-s', '--store', default=os.environ.get('STORE_ENV', 'staging'),
        choices=['staging', 'production', 'local'])

    # Macarroon restricting options.
    parser.add_argument(
        '-p', '--permission', action="append", dest='permissions',
        choices=[
            'edit_account',
            'modify_account_key',
            'package_access',
            'package_manage',
            'package_metrics',
            'package_push',
            'package_purchase',
            'package_register',
            'package_release',
            'package_update',
            'package_upload',
            'package_upload_request',
            'store_admin',
            'store_review',
        ])
    parser.add_argument(
        '-c', '--channel', action="append", dest='channels',
        choices=['stable', 'candidate', 'beta', 'edge'])

    # Request options.
    parser.add_argument('-H', '--header', action="append", default=[], dest='headers')
    parser.add_argument(
        '-X', '--method', default='GET', choices=['GET', 'PATCH', 'POST', 'PUT'])
    parser.add_argument('-d', '--data')

    parser.add_argument('url', nargs='?')

    args = parser.parse_args()

    auth_dir = os.path.abspath(os.environ.get('SNAP_USER_COMMON', '.'))

    if args.list_auth:
        print('Available credendials:')
        for fn in os.listdir(auth_dir):
            if fn.endswith('.surl'):
                ident = fn.replace('.surl', '')
                with open(os.path.join(auth_dir, fn)) as fd:
                    try:
                        a = json.load(fd)
                        store_env = a['store']
                    except json.decoder.JSONDecodeError:
                        continue
                print('  {} ({})'.format(ident, store_env))
        return 0

    if args.debug:
        # The http.client logger pollutes stdout.
        #from http.client import HTTPConnection
        #HTTPConnection.debuglevel = 1
        import logging
        handler = requests.packages.urllib3.add_stderr_logger()
        handler.setFormatter(logging.Formatter('\033[1m%(message)s\033[0m'))

    if args.auth:
        auth_path = os.path.join(auth_dir, args.auth + '.surl')
        legacy_path = os.path.join(auth_dir, args.auth)
        if os.path.exists(legacy_path):
            os.rename(legacy_path, auth_path)
        auth_exists = os.path.exists(auth_path)
    else:
        auth_path = None
        auth_exists = False

    if auth_exists and not args.force:
        with open(auth_path) as fd:
            try:
                a = json.load(fd)
                root, discharge, store_env = (a['root'], a['discharge'], a['store'])
            except json.decoder.JSONDecodeError:
                print('** Deprecated or Broken authentication file, '
                      'please delete it and login again:')
                print('  $ rm {}'.format(auth_path))
                return 1
    else:
        store_env = args.store
        if args.email is None:
            print('Needs "-e <email>" or $STORE_EMAIL.')
            return 1
        try:
            root, discharge = get_store_authorization(
                args.email, permissions=args.permissions,
                channels=args.channels, store_env=store_env)
        except Exception as e:
            print('Authorization failed! Double-check password and 2FA.')
            return 1
        if auth_path is not None:
            with open(auth_path, 'w') as fd:
                conf = {'root': root, 'discharge': discharge, 'store': store_env}
                json.dump(conf, fd, indent=2)

    authorization = get_authorization_header(root, discharge)
    headers = DEFAULT_HEADERS.copy()
    if args.url is None:
        url = '{}/dev/api/acl/verify/'.format(CONSTANTS[store_env]['sca_base_url'])
        data = {'auth_data': {'authorization': authorization}}
        method = 'POST'
    else:
        url = args.url
        if args.data is not None:
            if args.data.startswith('@'):
                with open(os.path.expanduser(args.data[1:])) as fd:
                    data = json.load(fd)
            else:
                data = json.loads(args.data)
            method = args.method
            if args.method == 'GET':
                method = 'POST'
        else:
            data = None
            method = args.method
        headers.update({'Authorization': authorization})
        for h in args.headers:
            try:
                k, v = [t.strip() for t in h.split(':')]
            except ValueError:
                print('Invalid header: "{}"'.format(h))
                return 1
            headers[k] = v

    if args.debug:
        print('\033[1m******** request headers ********\033[0m',
              file=sys.stderr, flush=True)
        for k, v in headers.items():
            print('{}: {}'.format(k, v), file=sys.stderr, flush=True)
        print('\033[1m**********************************\033[0m',
              file=sys.stderr, flush=True)

    response = requests.request(
        url=url, method=method, json=data, headers=headers, stream=True)

    # Refresh discharge if necessary.
    if response.headers.get('WWW-Authenticate') == (
            'Macaroon needs_refresh=1'):
        discharge = get_refreshed_discharge(discharge, store_env)
        with open(auth_path, 'w') as fd:
            conf = {'root': root, 'discharge': discharge, 'store': store_env}
            json.dump(conf, fd, indent=2)
            headers.update(
                {'Authorization': get_authorization_header(root, discharge)})
            response = requests.request(
                url=url, method=method, json=data, headers=headers, stream=True)

    if args.debug:
        print('\033[1m******** response headers ********\033[0m',
              file=sys.stderr, flush=True)
        print('HTTP/1.1 {} {}'.format(response.status_code, response.reason),
              file=sys.stderr, flush=True)
        for k, v in response.headers.items():
            print('{}: {}'.format(k, v), file=sys.stderr, flush=True)
        print('\033[1m**********************************\033[0m',
              file=sys.stderr, flush=True)

    chunks = [
        chunk for chunk in response.iter_content(chunk_size=1024 * 8) if chunk]
    result = b''.join(chunks)

    if args.pretty:
        # ensure result is json and dump it properly
        data = json.loads(result.decode('utf-8'))
        print(json.dumps(data, indent=4, sort_keys=True))
    else:
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()

    return 0


if __name__ == '__main__':
    sys.exit(main())
