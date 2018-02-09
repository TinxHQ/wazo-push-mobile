# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
from pyfcm import FCMNotification

from xivo_auth_client import Client as Auth


logging.basicConfig()


class Service:

    def load(self, dependencies):
        celery_app = dependencies['celery']
        self.config = dependencies['config']['mobile']
        self.token = self.get_token()
        print('Mobile push notification plugin is started')

        @celery_app.task
        def mobile_push_notification(subscription, event):
            user_uuid = subscription.get('events_user_uuid')
            token = self.get_external_token(user_uuid)['token']
            push = PushNotification(token, self.token, self.config)

            data = event.get('data')
            name = event.get('name')

            if name == 'chat_message_received':
               msg = {
                 'notification_type': 'messageReceived',
                 'items': data
               }
               push.send_notification(msg)

            if data.get('status'):
                if 'call_id' in data:
                    if name == 'call_created' and not data.get('is_caller'):
                        data['notification_type'] = 'incomingCall'
                        push.send_notification(data)

        self._callback = mobile_push_notification

    def get_token(self):
        auth = Auth(
            self.config['auth']['host'],
            username=self.config['auth']['username'],
            password=self.config['auth']['password'],
            verify_certificate=False)
        return auth.token.new('xivo_service', expiration=3600)

    def get_external_token(self, user_uuid):
        token = None
        auth = Auth(self.config['auth']['host'], verify_certificate=False, token=self.token['token'])
        try:
            token = auth.external.get('mobile', user_uuid)
        except:
            print('Request new token')
            self.token = self.get_token()
            auth = Auth(self.config['auth']['host'], verify_certificate=False, token=self.token['token'])
            token = auth.external.get('mobile', user_uuid)

        return token

    def callback(self):
        return self._callback


class PushNotification(object):

    def __init__(self, external_token, token_data, config):
        self.config = config
        self.token = external_token
        self.token_data = token_data

    def send_notification(self, data):

      push_service = FCMNotification(api_key=self.config['fcm']['api_key'])

      push_service.single_device_data_message(
          registration_id=self.token,
          data_message=data)
