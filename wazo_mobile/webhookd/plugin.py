# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
import uuid
from pyfcm import FCMNotification

from xivo_auth_client import Client as Auth
from wazo_webhookd.plugins.subscription.service import SubscriptionService


logging.basicConfig()
logger = logging.getLogger(__name__)


class Service:

    def load(self, dependencies):
        bus_consumer = dependencies['bus_consumer']
        celery_app = dependencies['celery']
        self.config = dependencies['config']['mobile']
        self.subscription_service = SubscriptionService(dependencies['config'])
        self.token = self.get_token()
        bus_consumer.subscribe_to_event_names(uuid=uuid.uuid4(),
                                              event_names=['auth_user_external_auth_added'],
                                              user_uuid=None,
                                              wazo_uuid=None,
                                              callback=self.on_external_auth_added)
        bus_consumer.subscribe_to_event_names(uuid=uuid.uuid4(),
                                              event_names=['auth_user_external_auth_deleted'],
                                              user_uuid=None,
                                              wazo_uuid=None,
                                              callback=self.on_external_auth_deleted)
        print('Mobile push notification plugin is started')

        @celery_app.task
        def mobile_push_notification(subscription, event):
            user_uuid = subscription.get('events_user_uuid')
            token = self.get_external_token(user_uuid)['token']
            push = PushNotification(token, self.token, self.config)

            msg = None
            data = event.get('data')
            name = event.get('name')

            if name == 'call_push_notification':
                msg = {
                     'notification_type': 'incomingCall',
                     'items': data
                }

            if name == 'chat_message_received':
                msg = {
                     'notification_type': 'messageReceived',
                     'items': data
                }

            if data.get('status'):
                if 'call_id' in data:
                    if name == 'call_created' and data.get('is_caller') != True:
                        msg = data
                        msg['notification_type'] = 'incomingCall'

            if msg:
                push.send_notification(msg)

        self._callback = mobile_push_notification

    def on_external_auth_added(self, body, event):
        if body['data'].get('external_auth_name') == 'mobile':
            user_uuid = body['data']['user_uuid']
            subscription = self.subscription_service.create({
                'name': 'Push notification mobile for user {}'.format(user_uuid),
                'service': 'mobile',
                'events': ['call_created', 'chat_message_received', 'call_push_notification'],
                'events_user_uuid': user_uuid,
                'owner_user_uuid': user_uuid,
                'config': {},
                'metadata': {'mobile': 'true'},
            })

    def on_external_auth_deleted(self, body, event):
        if body['data'].get('external_auth_name') == 'mobile':
            user_uuid = body['data']['user_uuid']
            subscriptions = self.subscription_service.list(owner_user_uuid=user_uuid, search_metadata={'mobile': 'true'})
            for subscription in subscriptions:
                self.subscription_service.delete(subscription.uuid)

    def get_token(self):
        auth = Auth(
            self.config['auth']['host'],
            username=self.config['auth']['username'],
            password=self.config['auth']['password'],
            verify_certificate=False)
        return auth.token.new('wazo_user', expiration=3600)

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

      notification = push_service.single_device_data_message(
          registration_id=self.token,
          data_message=data)

      if notification.get('failure') != 0:
          logger.error('Error to send push notification', notification)
