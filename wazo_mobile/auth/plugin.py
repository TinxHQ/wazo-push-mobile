# -*- coding: utf-8 -*-
# Copyright 2018 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging

from flask import request

from xivo.mallow import fields
from wazo_auth import exceptions, http, schemas


logger = logging.getLogger(__name__)


class MobilePostSchema(schemas.BaseSchema):

    token = fields.String(min=1, max=512)


class MobileAuth(http.AuthResource):

    auth_type = 'mobile'

    def __init__(self, external_auth_service, config):
        self.config = config

    @http.required_acl('auth.users.{user_uuid}.external.mobile.delete')
    def delete(self, user_uuid):
        self.external_auth_service.delete(user_uuid, self.auth_type)
        return '', 204

    @http.required_acl('auth.users.{user_uuid}.external.mobile.read')
    def get(self, user_uuid):
        data = self.external_auth_service.get(user_uuid, self.auth_type)
        return self._new_get_response(data)

    @http.required_acl('auth.users.{user_uuid}.external.mobile.create')
    def post(self, user_uuid):
        args, errors = MobilePostSchema().load(request.get_json())
        if errors:
            raise exceptions.UserParamException.from_errors(errors)

        logger.info('User(%s) is addong token for Mobile', str(user_uuid))
        data = {
            'token': args.get('token')
        }
        self.external_auth_service.create(user_uuid, self.auth_type, data)

        return {'token': args.get('token')}, 201

    @staticmethod
    def _new_get_response(data):
        return {
            'token': data.get('token')
        }, 200


class Plugin(object):

    def load(self, dependencies):
        api = dependencies['api']
        args = (dependencies['external_auth_service'], dependencies['config'])

        api.add_resource(MobileAuth,
            '/users/<uuid:user_uuid>/external/mobile',
            resource_class_args=args)
