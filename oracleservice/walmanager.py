from dataclasses import dataclass

import logging
import os


@dataclass
class WALManager:
    path_to_wal: str = 'oracleservice/staking_parameters.txt'
    content = []

    def read(self):
        with open(self.path_to_wal, 'r') as f:
            content = f.readlines()

        record = {}
        for line in content:
            if line.startswith('---'):
                record['approved'] = True
                self.content.append(record)
                record = {}
                continue

            eq_sign_index = line.find('=')
            key = line[:eq_sign_index]
            value = line[eq_sign_index + 1:].rstrip()
            record[key] = value

        if 'approved' in self.content[-1]:
            self.content.append(record)
            self.content[-1]['approved'] = False

        return self.content

    def write(self, msg=None):
        if not os.path.exists(self.path_to_wal):
            logging.getLogger().info(f"Create WAL: {self.path_to_wal}")

        with open(self.path_to_wal, 'a') as f:
            f.write(msg)
            os.fsync(f.fileno())

    def log_exists(self):
        return os.path.isfile(self.path_to_wal)

    def get_last_record(self):
        return self.read()[-1]

    def get_penultimate_record(self):
        try:
            return self.read()[-2]
        except IndexError:
            logging.getLogger().info('WAL contains only one record')
            return self.content[-1]
