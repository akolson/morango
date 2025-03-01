"""
Helper functions for use across syncing related functionality.
"""
import json
import uuid
from test.support import EnvironmentVarGuard

import factory
import mock
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test.testcases import LiveServerTestCase
from django.utils import timezone
from facility_profile.models import Facility
from facility_profile.models import InteractionLog
from facility_profile.models import MyUser
from facility_profile.models import SummaryLog

from morango.api.serializers import BufferSerializer
from morango.models.core import AbstractStore
from morango.models.core import Buffer
from morango.models.core import DatabaseIDModel
from morango.models.core import InstanceIDModel
from morango.models.core import RecordMaxCounter
from morango.models.core import RecordMaxCounterBuffer
from morango.models.core import Store
from morango.models.core import SyncSession
from morango.models.core import TransferSession
from morango.sync.context import SessionContext
from morango.sync.controller import MorangoProfileController
from morango.sync.controller import SessionController
from morango.sync.syncsession import NetworkSyncConnection
from morango.sync.syncsession import SyncSessionClient
from morango.sync.syncsession import TransferClient


class FacilityFactory(factory.DjangoModelFactory):
    class Meta:
        model = Facility

    name = factory.Sequence(lambda n: "Fac %d" % n)


class AbstractStoreFactory(factory.DjangoModelFactory):
    class Meta:
        model = AbstractStore

    model_name = "facility"
    profile = "facilitydata"


class BufferFactory(AbstractStoreFactory):
    class Meta:
        model = Buffer


class StoreFactory(AbstractStoreFactory):
    class Meta:
        model = Store


class RecordMaxCounterBufferFactory(factory.DjangoModelFactory):
    class Meta:
        model = RecordMaxCounterBuffer


class RecordMaxCounterFactory(factory.DjangoModelFactory):
    class Meta:
        model = RecordMaxCounter


def serialized_facility_factory(identifier):
    facility = Facility(name="Facility {}".format(identifier), id=identifier)
    return DjangoJSONEncoder().encode(facility.serialize())


def create_dummy_store_data():
    data = {}
    DatabaseIDModel.objects.create()
    data["group1_id"] = InstanceIDModel.get_or_create_current_instance()[
        0
    ]  # counter is at 0

    # create controllers for app/store/buffer operations
    conn = mock.Mock(spec='morango.sync.syncsession.NetworkSyncConnection')
    conn.server_info = dict(capabilities=[])
    data["mc"] = MorangoProfileController("facilitydata")
    data["sc"] = TransferClient(conn, "host", SessionController.build())
    session = SyncSession.objects.create(
        id=uuid.uuid4().hex,
        profile="facilitydata",
        last_activity_timestamp=timezone.now(),
    )
    data["tx"] = TransferSession.objects.create(
        id=uuid.uuid4().hex,
        sync_session=session,
        push=True,
        last_activity_timestamp=timezone.now(),
    )

    data["mc"].serialize_into_store()  # counter is at 1
    # create group of facilities and first serialization
    data["group1_c1"] = [FacilityFactory() for _ in range(5)]
    data["mc"].serialize_into_store()  # counter is at 2

    # create group of facilities and second serialization
    data["group1_c2"] = [FacilityFactory() for _ in range(5)]

    # create users and logs associated with user
    data["user1"] = MyUser.objects.create(username="bob")
    data["user1_sumlogs"] = [
        SummaryLog.objects.create(user=data["user1"]) for _ in range(5)
    ]

    data["mc"].serialize_into_store()  # counter is at 3

    # create new instance id and group of facilities
    with EnvironmentVarGuard() as env:
        env["MORANGO_SYSTEM_ID"] = "new_sys_id"

        data["group2_id"] = InstanceIDModel.get_or_create_current_instance(
            clear_cache=True
        )[
            0
        ]  # new counter is at 0

        data["mc"].serialize_into_store()  # new counter is at 1
        data["group2_c1"] = [FacilityFactory() for _ in range(5)]

        # create users and logs associated with user
        data["user2"] = MyUser.objects.create(username="rob")
        data["user2_sumlogs"] = [
            SummaryLog.objects.create(user=data["user2"]) for _ in range(5)
        ]
        data["user2_interlogs"] = [
            InteractionLog.objects.create(user=data["user2"]) for _ in range(5)
        ]

        data["user3"] = MyUser.objects.create(username="zob")
        data["user3_sumlogs"] = [
            SummaryLog.objects.create(user=data["user3"]) for _ in range(5)
        ]
        data["user3_interlogs"] = [
            InteractionLog.objects.create(user=data["user3"]) for _ in range(5)
        ]

        data["mc"].serialize_into_store()  # new counter is at 2

        data["user4"] = MyUser.objects.create(
            username="invalid", _morango_partition="badpartition"
        )
        data["mc"].serialize_into_store()  # new counter is at 3

    return data


def random_ids(count):
    return [uuid.uuid4().hex for _ in range(count)]


def create_rmc_data(c1, c2, c3, c4, ids, model_id):
    for i, c in zip(ids, [c1, c2, c3, c4]):
        RecordMaxCounterFactory(instance_id=i, counter=c, store_model_id=model_id)


def create_rmcb_data(c1, c2, c3, c4, ids, model_id, ts):
    for i, c in zip(ids, [c1, c2, c3, c4]):
        RecordMaxCounterBufferFactory(
            instance_id=i, counter=c, model_uuid=model_id, transfer_session_id=ts
        )


def create_buffer_and_store_dummy_data(transfer_session_id):
    data = {}
    common_id = [uuid.uuid4().hex]

    # example data for reverse ff
    data["model1"] = uuid.uuid4().hex
    data["model1_rmc_ids"] = common_id + random_ids(3)

    # store1: last_saved => D: 3
    # RMCs A: 3, B: 1, C: 2, D: 3
    StoreFactory(
        serialized="store",
        last_saved_instance=data["model1_rmc_ids"][3],
        last_saved_counter=3,
        id=data["model1"],
    )
    create_rmc_data(3, 1, 2, 3, data["model1_rmc_ids"], data["model1"])
    data["model1_rmcb_ids"] = common_id + random_ids(3)
    # buffer1: last_saved => A: 1
    # RMCBs A: 1, F: 2, G: 3, H: 4
    BufferFactory(
        serialized="buffer",
        last_saved_instance=data["model1_rmcb_ids"][0],
        last_saved_counter=1,
        model_uuid=data["model1"],
        transfer_session_id=transfer_session_id,
    )
    create_rmcb_data(
        1, 2, 3, 4, data["model1_rmcb_ids"], data["model1"], transfer_session_id
    )

    # example data for merge conflict (rmcb.counter > rmc.counter)
    data["model2"] = uuid.uuid4().hex
    data["model2_rmc_ids"] = common_id + random_ids(3)
    # store2: last_saved => C: 2
    # RMCs A: 1, B: 1, C: 2, D: 3
    StoreFactory(
        serialized="store",
        last_saved_instance=data["model2_rmc_ids"][2],
        last_saved_counter=2,
        id=data["model2"],
        conflicting_serialized_data="store",
    )
    create_rmc_data(1, 1, 2, 3, data["model2_rmc_ids"], data["model2"])
    data["model2_rmcb_ids"] = common_id + random_ids(3)
    # buffer2: last_saved => F: 2
    # RMCBs A: 3, F: 2, G: 3, H: 4
    BufferFactory(
        serialized="buffer",
        last_saved_instance=data["model2_rmcb_ids"][1],
        last_saved_counter=2,
        model_uuid=data["model2"],
        transfer_session_id=transfer_session_id,
        deleted=1,
    )
    create_rmcb_data(
        3, 2, 3, 4, data["model2_rmcb_ids"], data["model2"], transfer_session_id
    )

    # example data for merge conflict (rmcb.counter <= rmc.counter)
    data["model5"] = uuid.uuid4().hex
    data["model5_rmc_ids"] = common_id + random_ids(3)
    # store5: last_saved => C: 2
    # RMCs A: 3, B: 1, C: 2, D: 3
    StoreFactory(
        serialized="store",
        last_saved_instance=data["model5_rmc_ids"][2],
        last_saved_counter=2,
        id=data["model5"],
        conflicting_serialized_data="store",
    )
    create_rmc_data(3, 1, 2, 3, data["model5_rmc_ids"], data["model5"])
    data["model5_rmcb_ids"] = common_id + random_ids(3)
    # buffer5: last_saved => F: 2
    # RMCBs A: 1, F: 2, G: 3, H: 4
    BufferFactory(
        serialized="buffer",
        last_saved_instance=data["model5_rmcb_ids"][1],
        last_saved_counter=2,
        model_uuid=data["model5"],
        transfer_session_id=transfer_session_id,
    )
    create_rmcb_data(
        1, 2, 3, 4, data["model5_rmcb_ids"], data["model5"], transfer_session_id
    )

    # example data for merge conflict with hard delete(rmcb.counter <= rmc.counter)
    data["model7"] = uuid.uuid4().hex
    data["model7_rmc_ids"] = common_id + random_ids(3)
    # store5: last_saved => C: 2
    # RMCs A: 3, B: 1, C: 2, D: 3
    StoreFactory(
        serialized="store",
        last_saved_instance=data["model7_rmc_ids"][2],
        last_saved_counter=2,
        id=data["model7"],
        conflicting_serialized_data="store",
    )
    create_rmc_data(3, 1, 2, 3, data["model7_rmc_ids"], data["model7"])
    data["model7_rmcb_ids"] = common_id + random_ids(3)
    # buffer5: last_saved => F: 2
    # RMCBs A: 1, F: 2, G: 3, H: 4
    BufferFactory(
        serialized="",
        last_saved_instance=data["model7_rmcb_ids"][1],
        last_saved_counter=2,
        model_uuid=data["model7"],
        transfer_session_id=transfer_session_id,
        hard_deleted=True,
    )
    create_rmcb_data(
        1, 2, 3, 4, data["model7_rmcb_ids"], data["model7"], transfer_session_id
    )

    # example data for ff
    data["model3"] = uuid.uuid4().hex
    data["model3_rmc_ids"] = common_id + random_ids(3)
    # store3: last_saved => A: 1
    # RMCs A: 1, B: 2, C: 3, D: 4
    StoreFactory(
        serialized="store",
        last_saved_instance=data["model3_rmc_ids"][0],
        last_saved_counter=1,
        id=data["model3"],
    )
    create_rmc_data(1, 2, 3, 4, data["model3_rmc_ids"], data["model3"])
    data["model3_rmcb_ids"] = common_id + random_ids(3)
    # buffer3: last_saved => F: 2
    # RMCBs A: 3, F: 2, G: 3, H: 4
    BufferFactory(
        serialized="buffer",
        last_saved_instance=data["model3_rmcb_ids"][1],
        last_saved_counter=2,
        model_uuid=data["model3"],
        transfer_session_id=transfer_session_id,
    )
    create_rmcb_data(
        3, 2, 3, 4, data["model3_rmcb_ids"], data["model3"], transfer_session_id
    )

    # example for missing store data
    data["model4"] = uuid.uuid4().hex
    data["model4_rmcb_ids"] = common_id + random_ids(3)
    BufferFactory(
        serialized="buffer",
        last_saved_instance=data["model4_rmcb_ids"][0],
        last_saved_counter=1,
        model_uuid=data["model4"],
        transfer_session_id=transfer_session_id,
    )
    create_rmcb_data(
        1, 2, 3, 4, data["model4_rmcb_ids"], data["model4"], transfer_session_id
    )

    # buffer record with different transfer session id
    session = SyncSession.objects.create(
        id=uuid.uuid4().hex, profile="", last_activity_timestamp=timezone.now()
    )
    data["tfs_id"] = uuid.uuid4().hex
    TransferSession.objects.create(
        id=data["tfs_id"],
        sync_session=session,
        push=True,
        last_activity_timestamp=timezone.now(),
    )
    data["model6"] = uuid.uuid4().hex
    data["model6_rmcb_ids"] = common_id + random_ids(3)
    BufferFactory(
        last_saved_instance=data["model6_rmcb_ids"][0],
        last_saved_counter=1,
        model_uuid=data["model6"],
        transfer_session_id=data["tfs_id"],
    )
    create_rmcb_data(
        1, 2, 3, 4, data["model6_rmcb_ids"], data["model6"], data["tfs_id"]
    )

    return data


class BaseClientTestCase(LiveServerTestCase):
    profile = "facilitydata"

    def setUp(self):
        super(BaseClientTestCase, self).setUp()
        DatabaseIDModel.objects.create()
        self.session = SyncSession.objects.create(
            id=uuid.uuid4().hex,
            profile=self.profile,
            last_activity_timestamp=timezone.now(),
        )
        self.conn = NetworkSyncConnection(base_url=self.live_server_url)
        self.conn.chunk_size = 3
        self.transfer_session = None
        self.client = self.build_client()
        self.instance = InstanceIDModel.get_or_create_current_instance(clear_cache=True)[0]

    def build_client(self, client_class=SyncSessionClient, controller=None):
        client = client_class(self.conn, self.session, controller)
        self.transferring_mock = mock.Mock()
        client.signals.transferring.connect(self.transferring_mock)
        return client

    def build_buffer_items(self, transfer_session, **kwargs):
        data = {
            "profile": kwargs.get("profile", self.profile),
            "serialized": kwargs.get("serialized", '{"test": 99}'),
            "deleted": kwargs.get("deleted", False),
            "last_saved_instance": kwargs.get("last_saved_instance", uuid.uuid4().hex),
            "last_saved_counter": kwargs.get("last_saved_counter", 179),
            "partition": kwargs.get("partition", "partition"),
            "source_id": kwargs.get("source_id", uuid.uuid4().hex),
            "model_name": kwargs.get("model_name", "contentsummarylog"),
            "conflicting_serialized_data": kwargs.get(
                "conflicting_serialized_data", ""
            ),
            "model_uuid": kwargs.get("model_uuid", None),
            "transfer_session": transfer_session,
        }

        for i in range(self.conn.chunk_size):
            data["source_id"] = uuid.uuid4().hex
            data["model_uuid"] = SummaryLog.compute_namespaced_id(
                data["partition"], data["source_id"], data["model_name"]
            )
            Buffer.objects.create(**data)

        buffered_items = Buffer.objects.filter(
            transfer_session=transfer_session
        )
        serialized_records = BufferSerializer(buffered_items, many=True)
        return json.dumps(serialized_records.data)


class BaseTransferClientTestCase(BaseClientTestCase):
    def build_client(self, client_class=TransferClient, controller=None, update_context=False):
        if not self.transfer_session:
            self.transfer_session = TransferSession.objects.create(
                id=uuid.uuid4().hex,
                sync_session=self.session,
                filter="partition",
                push=True,
                last_activity_timestamp=timezone.now(),
                records_total=3,
            )

        client = super(BaseTransferClientTestCase, self).build_client(client_class=client_class, controller=controller)
        if client.context.is_push is None:
            client.context.update(is_push=self.transfer_session.push)
        if update_context:
            client.context.update(transfer_session=self.transfer_session)
        return client


class TestSessionContext(SessionContext):
    __test__ = False
    _stage = None
    _stage_status = None

    @property
    def stage(self):
        return self._stage

    @property
    def stage_status(self):
        return self._stage_status

    def update_state(self, stage=None, stage_status=None):
        self._stage = stage or self._stage
        self._stage_status = stage_status or self._stage_status


class TestMigrationsMixin(object):
    # Modified from https://www.caktusgroup.com/blog/2016/02/02/writing-unit-tests-django-migrations/

    migrate_from = None
    migrate_to = None
    app = None

    @classmethod
    def setUpClass(cls):
        super(TestMigrationsMixin, cls).setUpClass()

        # get the latest migration before starting
        latest_migration = MigrationRecorder.Migration.objects.filter(app=cls.app).last()
        cls.latest_migration = (cls.app, latest_migration.name)

    def setUp(self):
        assert (
            self.migrate_from and self.migrate_to
        ), "TestCase '{}' must define migrate_from and migrate_to properties".format(
            type(self).__name__
        )

        migrate_from = [(self.app, self.migrate_from)]
        migrate_to = [(self.app, self.migrate_to)]
        executor = MigrationExecutor(connection)
        old_apps = executor.loader.project_state(migrate_from).apps

        # Reverse to the original migration
        executor.migrate(migrate_from)

        self.setUpBeforeMigration(old_apps)

        # Run the migration to test
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()  # reload.
        executor.migrate(migrate_to)

        self.apps = executor.loader.project_state(migrate_to).apps

    def setUpBeforeMigration(self, apps):
        pass

    @classmethod
    def tearDownClass(cls):
        # revert migration back to latest migration
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([cls.latest_migration])

        super(TestMigrationsMixin, cls).tearDownClass()
