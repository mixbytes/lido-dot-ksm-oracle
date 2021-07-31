from oracleservice.walmanager import WALManager

import os
import pytest


path_to_wal = 'tests/test_wal.txt'
wal_manager = WALManager(path_to_wal)


@pytest.fixture
def create_wal_file():
    with open(path_to_wal, 'x') as f:
        yield f

    os.remove(path_to_wal)


@pytest.fixture
def write_to_wal(create_wal_file):
    wal_manager.write('test=value\n')


def test_WAL_exists(create_wal_file):
    assert wal_manager.log_exists()


def test_read_from_WAL(write_to_wal):
    content = wal_manager.read()

    assert content[0]['test'] == 'value'


def test_read_from_WALL_if_not_exists():
    content = wal_manager.read()
    assert content is None


def test_write(create_wal_file):
    wal_manager.write('test=value\n')

    content = wal_manager.read()
    assert content[0]['test'] == 'value'
