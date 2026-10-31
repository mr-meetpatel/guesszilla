"""OpenAPI schemas for generated frontend resource contracts."""

from marshmallow import Schema, fields


class RulesSchema(Schema):
    timezone = fields.String(required=True)
    weekdays = fields.List(fields.Integer(), required=True)
    threshold = fields.Integer(required=True)
    announce = fields.Boolean(required=True)
    title = fields.String(required=True)
    reactions = fields.Dict(keys=fields.String(), values=fields.String(), required=True)


class WorkspaceSchema(Schema):
    id = fields.String(required=True)
    name = fields.String(required=True)
    active = fields.Boolean(required=True)
    connected = fields.Boolean(required=True)
    scopes = fields.String(required=True)
    updated_at = fields.String()


class ChannelSchema(Schema):
    id = fields.String(required=True)
    workspace_id = fields.String(required=True)
    slack_id = fields.String(required=True)
    name = fields.String(required=True)
    active = fields.Boolean(required=True)
    rules = fields.Nested(RulesSchema, required=True)
    version = fields.Integer(required=True)
    has_password = fields.Boolean(required=True)
    roster_synced_at = fields.String(allow_none=True, required=True)


class PlayerSchema(Schema):
    user_id = fields.String(required=True)
    name = fields.String(required=True)
    days = fields.Integer()
    dates = fields.List(fields.String())
    included = fields.Boolean()
    active = fields.Boolean()


class SnapshotSchema(Schema):
    start = fields.String(required=True)
    end = fields.String(required=True)
    timezone = fields.String(required=True)
    threshold = fields.Integer(required=True)
    eligible_days = fields.Integer(required=True)
    eligible_dates = fields.List(fields.String(), required=True)
    players = fields.List(fields.Nested(PlayerSchema), required=True)
    roster_size = fields.Integer(required=True)
    participating = fields.Integer(required=True)
    ratio = fields.Float(allow_none=True, required=True)
    royalties = fields.Integer(required=True)
    cutoff = fields.String(required=True)
    roster_synced_at = fields.String(allow_none=True)
    announce = fields.Boolean()
    title = fields.String()
    rules_version = fields.Integer()
    destination = fields.String()


class ReportSchema(Schema):
    id = fields.String(required=True)
    channel_id = fields.String(required=True)
    start = fields.String(required=True)
    end = fields.String(required=True)
    revision = fields.Integer(required=True)
    status = fields.String(required=True)
    snapshot = fields.Nested(SnapshotSchema, required=True)
    created_at = fields.String(required=True)


class WorkspaceList(Schema):
    data = fields.List(fields.Nested(WorkspaceSchema), required=True)


class ChannelList(Schema):
    data = fields.List(fields.Nested(ChannelSchema), required=True)


class ChannelResponse(Schema):
    data = fields.Nested(ChannelSchema, required=True)


class SnapshotResponse(Schema):
    data = fields.Nested(SnapshotSchema, required=True)


class ReportResponse(Schema):
    data = fields.Nested(ReportSchema, required=True)


class ReportList(Schema):
    data = fields.List(fields.Nested(ReportSchema), required=True)
    cursor = fields.Integer(allow_none=True)
