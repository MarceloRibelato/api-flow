"""Setup pg_partman monthly partitions for 4 tables and 360-day data retention

Revision ID: p6a7r8t9m0a1
Revises: z9z9z9z9z9za
Create Date: 2026-09-07 15:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

revision = 'p6a7r8t9m0a1'
down_revision = 'c0a1p2a3b4i5'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 1. Add retention_days to companies table
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS retention_days INTEGER DEFAULT 360")
    op.execute("UPDATE companies SET retention_days = 360 WHERE retention_days IS NULL")

    if dialect == "postgresql":
        # 2. Setup pg_partman schema and extension if available
        try:
            has_partman = bool(bind.execute(text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_partman'")).scalar())
        except Exception:
            has_partman = False

        if has_partman:
            try:
                op.execute("CREATE SCHEMA IF NOT EXISTS partman")
                op.execute("CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman")
            except Exception as e:
                print(f"Notice: Could not enable pg_partman: {e}")

        # Check if pg_partman is active
        try:
            is_partman_active = bool(bind.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_partman'")).scalar())
        except Exception:
            is_partman_active = False

        # 3. Drop legacy history tables (approved by user to clear old history)
        op.execute("DROP TABLE IF EXISTS api_test_execution_history_archive CASCADE")
        op.execute("DROP TABLE IF EXISTS api_test_execution_history CASCADE")

        # 4. Create 3 partitioned execution history tables
        tables = [
            ("api_execution_history", "api"),
            ("web_execution_history", "web"),
            ("mobile_execution_history", "mob"),
        ]

        for table_name, prefix in tables:
            op.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id BIGSERIAL,
                    execution_id VARCHAR(255),
                    batch_id VARCHAR(100),
                    api_id VARCHAR(100),
                    api_name VARCHAR(255),
                    project_id INTEGER,
                    flow_id VARCHAR(100),
                    node_id VARCHAR(100),
                    schedule_id INTEGER,
                    feature_name VARCHAR(255),
                    node_name VARCHAR(255),
                    user_id INTEGER,
                    environment_id INTEGER,
                    environment_name VARCHAR(100),
                    method VARCHAR(10),
                    url TEXT,
                    request_headers JSON,
                    request_body TEXT,
                    request_params JSON,
                    status_code INTEGER,
                    status_text VARCHAR(100),
                    response_headers JSON,
                    response_body TEXT,
                    response_time INTEGER,
                    error_message TEXT,
                    variables_used JSON,
                    processed_url TEXT,
                    assertions JSON,
                    video_url VARCHAR(500),
                    healed_selector VARCHAR(500),
                    execution_type VARCHAR(50) DEFAULT '{table_name.split('_')[0]}',
                    trigger_origin VARCHAR(50) DEFAULT 'manual',
                    retry_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE,
                    PRIMARY KEY (id, created_at)
                ) PARTITION BY RANGE (created_at);
            """)

            op.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}_lookup ON {table_name} (user_id, project_id, created_at)")
            op.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}_env_lookup ON {table_name} (user_id, environment_id, created_at)")
            op.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}_schedule_time ON {table_name} (schedule_id, created_at)")
            op.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}_project_time ON {table_name} (project_id, created_at)")
            op.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}_batch_time ON {table_name} (batch_id, created_at)")
            op.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}_exec_id ON {table_name} (execution_id)")
            op.execute(f"CREATE INDEX IF NOT EXISTS idx_{prefix}_created_at ON {table_name} (created_at DESC)")

            # Register with pg_partman if active
            if is_partman_active:
                try:
                    is_registered = bool(bind.execute(text(f"SELECT 1 FROM partman.part_config WHERE parent_table = 'public.{table_name}'")).scalar())
                    if not is_registered:
                        op.execute(f"""
                            SELECT partman.create_parent(
                                p_parent_table => 'public.{table_name}',
                                p_control => 'created_at',
                                p_type => 'native',
                                p_interval => '1 month',
                                p_premake => 4
                            );
                        """)
                    op.execute(f"""
                        UPDATE partman.part_config
                        SET retention = '360 days',
                            retention_keep_table = false
                        WHERE parent_table = 'public.{table_name}';
                    """)
                except Exception as e:
                    print(f"Notice: pg_partman registration for {table_name}: {e}")
            else:
                # Create a default partition so inserts work even if partman is not active yet
                op.execute(f"CREATE TABLE IF NOT EXISTS {table_name}_default PARTITION OF {table_name} DEFAULT")

        # 5. Partition performance_test_results
        op.execute("DROP TABLE IF EXISTS performance_test_results CASCADE")
        op.execute("""
            CREATE TABLE IF NOT EXISTS performance_test_results (
                id VARCHAR(50) NOT NULL,
                flow_id INTEGER,
                company_id INTEGER,
                user_id INTEGER,
                status VARCHAR(20) DEFAULT 'running',
                test_name VARCHAR(255),
                target_url VARCHAR(500),
                virtual_users INTEGER DEFAULT 1,
                iterations INTEGER DEFAULT 1,
                duration_seconds INTEGER,
                ramp_up_seconds INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                success_requests INTEGER DEFAULT 0,
                failed_requests INTEGER DEFAULT 0,
                avg_latency FLOAT DEFAULT 0.0,
                min_latency FLOAT DEFAULT 0.0,
                max_latency FLOAT DEFAULT 0.0,
                p50_latency FLOAT DEFAULT 0.0,
                p90_latency FLOAT DEFAULT 0.0,
                p95_latency FLOAT DEFAULT 0.0,
                p99_latency FLOAT DEFAULT 0.0,
                requests_per_second FLOAT DEFAULT 0.0,
                time_series_data JSON,
                api_stats JSON,
                failed_requests_detail JSON,
                started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
                completed_at TIMESTAMP WITH TIME ZONE,
                PRIMARY KEY (id, started_at)
            ) PARTITION BY RANGE (started_at);
        """)

        op.execute("CREATE INDEX IF NOT EXISTS idx_perf_started_at ON performance_test_results (started_at DESC)")
        op.execute("CREATE INDEX IF NOT EXISTS idx_perf_company_started ON performance_test_results (company_id, started_at)")
        op.execute("CREATE INDEX IF NOT EXISTS idx_perf_flow_started ON performance_test_results (flow_id, started_at)")

        # Register performance_test_results with pg_partman if active
        if is_partman_active:
            try:
                is_registered = bool(bind.execute(text("SELECT 1 FROM partman.part_config WHERE parent_table = 'public.performance_test_results'")).scalar())
                if not is_registered:
                    op.execute("""
                        SELECT partman.create_parent(
                            p_parent_table => 'public.performance_test_results',
                            p_control => 'started_at',
                            p_type => 'native',
                            p_interval => '1 month',
                            p_premake => 4
                        );
                    """)
                op.execute("""
                    UPDATE partman.part_config
                    SET retention = '360 days',
                        retention_keep_table = false
                    WHERE parent_table = 'public.performance_test_results';
                """)
            except Exception as e:
                print(f"Notice: pg_partman registration for performance_test_results: {e}")
        else:
            op.execute("CREATE TABLE IF NOT EXISTS performance_test_results_default PARTITION OF performance_test_results DEFAULT")

    else:
        # SQLite / Generic database fallback (e.g. for testing)
        for tname in ("api_execution_history", "web_execution_history", "mobile_execution_history"):
            op.create_table(
                tname,
                sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column('execution_id', sa.String(), index=True),
                sa.Column('batch_id', sa.String(length=100), nullable=True, index=True),
                sa.Column('api_id', sa.String(length=100), nullable=True, index=True),
                sa.Column('api_name', sa.String(length=255), nullable=True),
                sa.Column('project_id', sa.Integer(), nullable=True, index=True),
                sa.Column('flow_id', sa.String(length=100), nullable=True, index=True),
                sa.Column('node_id', sa.String(length=100), nullable=True, index=True),
                sa.Column('schedule_id', sa.Integer(), nullable=True, index=True),
                sa.Column('feature_name', sa.String(length=255), nullable=True),
                sa.Column('node_name', sa.String(length=255), nullable=True),
                sa.Column('user_id', sa.Integer(), nullable=True, index=True),
                sa.Column('environment_id', sa.Integer(), nullable=True, index=True),
                sa.Column('environment_name', sa.String(length=100), nullable=True),
                sa.Column('method', sa.String(length=10), index=True),
                sa.Column('url', sa.Text()),
                sa.Column('request_headers', sa.JSON(), nullable=True),
                sa.Column('request_body', sa.Text(), nullable=True),
                sa.Column('request_params', sa.JSON(), nullable=True),
                sa.Column('status_code', sa.Integer(), index=True),
                sa.Column('status_text', sa.String(length=100)),
                sa.Column('response_headers', sa.JSON(), nullable=True),
                sa.Column('response_body', sa.Text(), nullable=True),
                sa.Column('response_time', sa.Integer()),
                sa.Column('error_message', sa.Text(), nullable=True),
                sa.Column('variables_used', sa.JSON(), nullable=True),
                sa.Column('processed_url', sa.Text(), nullable=True),
                sa.Column('assertions', sa.JSON(), nullable=True),
                sa.Column('video_url', sa.String(length=500), nullable=True),
                sa.Column('healed_selector', sa.String(length=500), nullable=True),
                sa.Column('execution_type', sa.String(length=50), index=True, default=tname.split('_')[0]),
                sa.Column('trigger_origin', sa.String(length=50), default="manual", index=True),
                sa.Column('retry_count', sa.Integer(), default=0, nullable=False),
                sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
                sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True)
            )


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        for tname in ("api_execution_history", "web_execution_history", "mobile_execution_history", "performance_test_results"):
            op.execute(f"DROP TABLE IF EXISTS {tname} CASCADE")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS retention_days")

