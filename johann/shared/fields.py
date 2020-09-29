# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import re
from typing import Any, Optional

import marshmallow

from johann.shared.enums import TaskState


class NameField(marshmallow.fields.String):
    """A name field."""

    default_error_messages = {
        "invalid_name": (
            "Names may only consist of letters, numbers, underscores, and hyphens"
        )
    }

    def _validated(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str) and re.match(r"[\w\-]+", value):
            return value
        else:
            raise self.make_error("invalid_name")

    def _deserialize(self, value, attr, data, **kwargs) -> Optional[str]:
        return self._validated(value)


class LaxStringField(marshmallow.fields.String):
    """A string field that will attempt to cast non-strings."""

    default_error_messages = {"invalid_string": "neither a string nor castable to one"}

    def _validated(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return str(value)
        except (ValueError, AttributeError, TypeError) as e:
            raise self.make_error("invalid_string") from e

    def _deserialize(self, value, attr, data, **kwargs) -> Optional[str]:
        return self._validated(value)


class StateField(marshmallow.fields.Field):
    def _serialize(self, value, attr, obj, **kwargs):
        if type(value) is str:
            return value
        else:
            return value.value

    def _deserialize(self, value, attr, data, **kwargs):
        try:
            return TaskState(value)
        except ValueError:
            raise marshmallow.ValidationError("not a valid TaskState")
