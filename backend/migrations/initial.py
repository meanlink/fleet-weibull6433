from sqlalchemy import inspect, text
from src.config.database import Base, engine
from src import models
def migrate():
    Base.metadata.create_all(engine)
    # 轻量原地升级：早期库可能缺少后续版本新增的列。
    inspector = inspect(engine)
    expected = {
        'jobs': {
            'stale': "ALTER TABLE jobs ADD COLUMN stale BOOLEAN NOT NULL DEFAULT FALSE",
            'dataset_checksum': "ALTER TABLE jobs ADD COLUMN dataset_checksum VARCHAR(64) NOT NULL DEFAULT ''",
        },
    }
    with engine.begin() as conn:
        existing_tables = inspector.get_table_names()
        for table, columns in expected.items():
            if table not in existing_tables:
                continue
            present = {col['name'] for col in inspector.get_columns(table)}
            for column, ddl in columns.items():
                if column not in present:
                    conn.execute(text(ddl))
if __name__=='__main__':migrate()
