from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest

from pruefversand.config import DatabaseSettings, load_settings
from pruefversand.cli import main
from pruefversand.database import (
    CsvImportRejected,
    apply_migration,
    connect_mysql,
    import_baustellen_csv,
    probe_mysql,
)
from pruefversand.models import ProtokollStatus
from pruefversand.home_demo import (
    HomeDemoCrashAfterProvider,
    HomeDemoControlledInterruption,
    prepare_ready_mock_versand,
    run_home_demo,
    run_manual_review_demo,
)
from pruefversand.manual_review import (
    ManualReviewError,
    assign_manual_review,
    ignore_manual_review,
    list_manual_reviews,
)
from pruefversand.manual_versand import (
    ManualVersandError,
    record_manual_urgent_versand,
)
from pruefversand.mysql_scanner import scan_and_persist
from pruefversand.pdf_tools import calculate_sha256
from pruefversand.versand_review import list_versand_review_incidents
from pruefversand.versand_resolution import (
    VersandResolutionError,
    confirm_provider_accepted,
    keep_provider_result_unknown,
    retry_after_provider_not_accepted,
)
from scripts.create_sample_pdf import build_pdf_bytes


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = (
    PROJECT_ROOT / 'sql/migrations/001_initial_schema.sql',
    PROJECT_ROOT / 'sql/migrations/002_scan_observation.sql',
    PROJECT_ROOT / 'sql/migrations/003_manual_review_decision.sql',
    PROJECT_ROOT / 'sql/migrations/004_versand_review_decision.sql',
)
SAMPLE_CSV = PROJECT_ROOT / 'sample_data/baustellen_example.csv'
PATTERN = (
    r'^(?P<auftragsnummer>[A-Za-z0-9]+)_'
    r'(?P<description>.+)\.pdf$'
)
HEADER = (
    'auftragsnummer,baustelle_name,kunde_name,kontakt_name,'
    'email,empfaengertyp,aktiv\n'
)


@unittest.skipUnless(
    os.environ.get('PRUEFVERSAND_MYSQL_TEST') == '1',
    'test MySQL HOME non active',
)
class MySqlIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_name = os.environ.get(
            'PRUEFVERSAND_MYSQL_TEST_DATABASE',
            'pruefversand_test',
        )
        if not database_name.endswith('_test'):
            raise RuntimeError(
                'la base integration doit se terminer par _test'
            )
        cls.settings = DatabaseSettings(
            host=os.environ.get(
                'PRUEFVERSAND_MYSQL_TEST_HOST',
                '127.0.0.1',
            ),
            port=int(
                os.environ.get('PRUEFVERSAND_MYSQL_TEST_PORT', '3307')
            ),
            name=database_name,
            user=os.environ.get(
                'PRUEFVERSAND_MYSQL_TEST_USER',
                'pruefversand_test',
            ),
            password_env='PRUEFVERSAND_MYSQL_TEST_PASSWORD',
            require_tls=False,
        )
        cls.connection = connect_mysql(cls.settings)
        cursor = cls.connection.cursor()
        cursor.execute('SELECT VERSION()')
        version = str(cursor.fetchone()[0])
        cursor.close()
        if not version.startswith('8.4.'):
            cls.connection.close()
            raise RuntimeError(
                f'MySQL 8.4 requis pour integration, recu {version}'
            )
        for migration in MIGRATIONS:
            apply_migration(cls.connection, migration)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def setUp(self) -> None:
        cursor = self.connection.cursor()
        for table in (
            'versand_review_decision',
            'versand_protokoll',
            'versand',
            'manual_review_decision',
            'scan_observation',
            'protokoll',
            'audit_event',
            'csv_import',
            'empfaenger',
            'auftrag',
            'baustelle',
        ):
            cursor.execute(f'DELETE FROM {table}')
        self.connection.commit()
        cursor.close()

    def _write_csv(self, content: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'synthetic.csv'
        path.write_text(content, encoding='utf-8')
        return path

    def _write_home_config(self, nas_path: Path) -> Path:
        source = (
            PROJECT_ROOT / 'config/home.example.toml'
        ).read_text(encoding='utf-8')
        source = source.replace(
            'nas_path = "sample_data/nas_mock"',
            f"nas_path = '{nas_path.as_posix()}'",
        )
        source = source.replace(
            'port = 3307',
            f'port = {self.settings.port}',
        )
        source = source.replace(
            'name = "pruefversand"',
            f'name = "{self.settings.name}"',
        )
        source = source.replace(
            'user = "pruefversand_app"',
            f'user = "{self.settings.user}"',
        )
        source = source.replace(
            'password_env = "MYSQL_PASSWORD"',
            'password_env = "PRUEFVERSAND_MYSQL_TEST_PASSWORD"',
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'home.mysql.test.toml'
        path.write_text(source, encoding='utf-8')
        return path

    def _scalar(self, sql: str, parameters: tuple[object, ...] = ()) -> object:
        cursor = self.connection.cursor()
        cursor.execute(sql, parameters)
        value = cursor.fetchone()[0]
        cursor.close()
        return value

    def _row(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> tuple[object, ...]:
        cursor = self.connection.cursor()
        cursor.execute(sql, parameters)
        value = tuple(cursor.fetchone())
        cursor.close()
        return value

    def _create_timeout_incident(
        self,
        label: str,
    ) -> tuple[object, Path, Path, int]:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        pdf = root / f'00125083_{label}.pdf'
        pdf.write_bytes(build_pdf_bytes(f'synthetic {label}'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )
        scan_and_persist(self.connection, root, PATTERN)
        scan_and_persist(self.connection, root, PATTERN)
        preparation = prepare_ready_mock_versand(
            self.connection,
            settings,
            simulate_timeout=True,
        )
        self.assertEqual(len(preparation.manual_review_incidents), 1)
        versand_id = int(self._scalar('SELECT id FROM versand'))
        self.connection.commit()
        return settings, pdf, outbox, versand_id

    def test_mysql_health_probe_is_read_only(self) -> None:
        before = self._row(
            '''
            SELECT
                (SELECT COUNT(*) FROM protokoll),
                (SELECT COUNT(*) FROM versand),
                (SELECT COUNT(*) FROM audit_event)
            '''
        )

        result = probe_mysql(self.settings)

        after = self._row(
            '''
            SELECT
                (SELECT COUNT(*) FROM protokoll),
                (SELECT COUNT(*) FROM versand),
                (SELECT COUNT(*) FROM audit_event)
            '''
        )
        self.assertTrue(result.server_version.startswith('8.4.'))
        self.assertEqual(after, before)

    def test_migration_is_idempotent_and_recorded(self) -> None:
        first = [
            apply_migration(self.connection, migration)
            for migration in MIGRATIONS
        ]
        second = [
            apply_migration(self.connection, migration)
            for migration in MIGRATIONS
        ]

        self.assertTrue(
            all(result.statements_executed > 0 for result in first)
        )
        self.assertEqual(
            [result.statements_executed for result in second],
            [result.statements_executed for result in first],
        )
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*)
                FROM schema_migration
                WHERE version IN (
                    '001_initial_schema',
                    '002_scan_observation',
                    '003_manual_review_decision',
                    '004_versand_review_decision'
                )
                '''
            ),
            4,
        )

    def test_versand_review_confirms_provider_accepted_without_retry(
        self,
    ) -> None:
        settings, _, outbox, versand_id = self._create_timeout_incident(
            'CONFIRM_ACCEPTED'
        )

        result = confirm_provider_accepted(
            self.connection,
            settings,
            versand_id,
            'Fournisseur mock confirme ACCEPTED',
        )

        self.assertEqual(result.decision, 'PROVIDER_ACCEPTED')
        self.assertEqual(result.versand_status, 'ACCEPTED')
        self.assertFalse(result.retry_performed)
        self.assertIsNone(result.message_path)
        self.assertEqual(len(list(outbox.glob('*.eml'))), 0)
        self.assertEqual(
            self._row(
                '''
                SELECT
                    v.status,
                    v.versuch_anzahl,
                    v.accepted_at IS NOT NULL,
                    p.status,
                    d.decision,
                    d.reason,
                    d.decided_at IS NOT NULL
                FROM versand AS v
                JOIN versand_protokoll AS vp ON vp.versand_id = v.id
                JOIN protokoll AS p ON p.id = vp.protokoll_id
                JOIN versand_review_decision AS d
                    ON d.versand_id = v.id
                '''
            ),
            (
                'ACCEPTED',
                1,
                1,
                'ACCEPTED',
                'PROVIDER_ACCEPTED',
                'Fournisseur mock confirme ACCEPTED',
                1,
            ),
        )
        self.connection.commit()
        with self.assertRaises(VersandResolutionError):
            retry_after_provider_not_accepted(
                self.connection,
                settings,
                versand_id,
                'Retry interdit',
            )

    def test_versand_review_keeps_unknown_and_records_decision(self) -> None:
        settings, _, outbox, versand_id = self._create_timeout_incident(
            'KEEP_UNKNOWN'
        )

        result = keep_provider_result_unknown(
            self.connection,
            settings,
            versand_id,
            'Resultat fournisseur toujours inconnu',
        )

        self.assertEqual(result.decision, 'KEEP_UNKNOWN')
        self.assertEqual(result.versand_status, 'MANUAL_REVIEW')
        self.assertFalse(result.retry_performed)
        self.assertEqual(len(list(outbox.glob('*.eml'))), 0)
        self.assertEqual(
            self._row(
                '''
                SELECT
                    v.status,
                    v.versuch_anzahl,
                    p.status,
                    d.decision,
                    d.reason,
                    d.decided_at IS NOT NULL
                FROM versand AS v
                JOIN versand_protokoll AS vp ON vp.versand_id = v.id
                JOIN protokoll AS p ON p.id = vp.protokoll_id
                JOIN versand_review_decision AS d
                    ON d.versand_id = v.id
                '''
            ),
            (
                'MANUAL_REVIEW',
                1,
                'MANUAL_REVIEW',
                'KEEP_UNKNOWN',
                'Resultat fournisseur toujours inconnu',
                1,
            ),
        )
        self.connection.commit()
        with self.assertRaises(VersandResolutionError):
            keep_provider_result_unknown(
                self.connection,
                settings,
                versand_id,
                'Decision dupliquee',
            )

    def test_versand_review_allows_exactly_one_manual_mock_retry(
        self,
    ) -> None:
        settings, pdf, outbox, versand_id = self._create_timeout_incident(
            'MANUAL_RETRY'
        )

        result = retry_after_provider_not_accepted(
            self.connection,
            settings,
            versand_id,
            'Fournisseur mock confirme non accepte',
        )

        self.assertEqual(
            result.decision,
            'PROVIDER_NOT_ACCEPTED_RETRY',
        )
        self.assertTrue(result.retry_performed)
        self.assertEqual(result.versand_status, 'ACCEPTED')
        self.assertIsNotNone(result.message_path)
        eml_files = list(outbox.glob('*.eml'))
        self.assertEqual(len(eml_files), 1)
        message = BytesParser(policy=policy.default).parsebytes(
            eml_files[0].read_bytes()
        )
        recipients = [
            address
            for _, address in getaddresses(
                [str(message['To'] or ''), str(message['Cc'] or '')]
            )
            if address
        ]
        self.assertTrue(recipients)
        self.assertTrue(
            all(address.endswith('@example.invalid') for address in recipients)
        )
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), pdf.name)
        self.assertEqual(
            attachments[0].get_payload(decode=True),
            pdf.read_bytes(),
        )
        self.assertEqual(
            self._row(
                '''
                SELECT
                    v.status,
                    v.versuch_anzahl,
                    p.status,
                    d.decision,
                    d.reason
                FROM versand AS v
                JOIN versand_protokoll AS vp ON vp.versand_id = v.id
                JOIN protokoll AS p ON p.id = vp.protokoll_id
                JOIN versand_review_decision AS d
                    ON d.versand_id = v.id
                '''
            ),
            (
                'ACCEPTED',
                2,
                'ACCEPTED',
                'PROVIDER_NOT_ACCEPTED_RETRY',
                'Fournisseur mock confirme non accepte',
            ),
        )
        self.connection.commit()
        with self.assertRaises(VersandResolutionError):
            retry_after_provider_not_accepted(
                self.connection,
                settings,
                versand_id,
                'Deuxieme retry interdit',
            )
        self.assertEqual(len(list(outbox.glob('*.eml'))), 1)

    def test_versand_review_retry_rejects_changed_sha256(self) -> None:
        settings, pdf, outbox, versand_id = self._create_timeout_incident(
            'SHA_CHANGED'
        )
        pdf.write_bytes(build_pdf_bytes('synthetic changed after timeout'))

        with self.assertRaisesRegex(VersandResolutionError, 'SHA-256'):
            retry_after_provider_not_accepted(
                self.connection,
                settings,
                versand_id,
                'Fournisseur confirme non accepte',
            )

        self.assertEqual(len(list(outbox.glob('*.eml'))), 0)
        self.assertEqual(
            self._row(
                '''
                SELECT
                    v.status,
                    v.versuch_anzahl,
                    p.status,
                    (SELECT COUNT(*) FROM versand_review_decision)
                FROM versand AS v
                JOIN versand_protokoll AS vp ON vp.versand_id = v.id
                JOIN protokoll AS p ON p.id = vp.protokoll_id
                '''
            ),
            ('MANUAL_REVIEW', 1, 'MANUAL_REVIEW', 0),
        )

    def test_manual_urgent_versand_excludes_pdf_from_weekly_batch(
        self,
    ) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        manual_pdf = root / '00125083_URGENT_MANUAL.pdf'
        automatic_pdf = root / '00125083_URGENT_AUTOMATIC.pdf'
        manual_pdf.write_bytes(build_pdf_bytes('synthetic urgent manual'))
        automatic_pdf.write_bytes(
            build_pdf_bytes('synthetic urgent automatic')
        )
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )
        first_scan = scan_and_persist(self.connection, root, PATTERN)
        second_scan = scan_and_persist(self.connection, root, PATTERN)
        self.assertEqual(
            {item.status for item in first_scan.report.items},
            {ProtokollStatus.DISCOVERED},
        )
        self.assertEqual(
            {item.status for item in second_scan.report.items},
            {ProtokollStatus.READY},
        )
        manual_id = int(
            self._scalar(
                'SELECT id FROM protokoll WHERE dateiname = %s',
                (manual_pdf.name,),
            )
        )
        self.connection.commit()
        eml_before = len(list(outbox.glob('*.eml')))
        manual_at = datetime(
            2026,
            8,
            3,
            10,
            0,
            tzinfo=timezone.utc,
        )

        manual_result = record_manual_urgent_versand(
            self.connection,
            settings,
            manual_id,
            'Utilisateur HOME',
            'Versand urgent synthétique',
            sent_at=manual_at,
        )

        self.assertEqual(manual_result.protokoll_status, 'MANUALLY_SENT')
        self.assertEqual(manual_result.sha256, calculate_sha256(manual_pdf))
        self.assertTrue(
            all(
                address.endswith('@example.invalid')
                for address in manual_result.recipients
            )
        )
        self.assertEqual(len(list(outbox.glob('*.eml'))), eml_before)
        row = self._row(
            '''
            SELECT
                v.modus,
                v.status,
                v.mail_provider,
                v.manual_actor,
                v.manual_reason,
                v.sending_started_at,
                p.status,
                p.sha256,
                v.empfaenger_snapshot
            FROM versand AS v
            JOIN versand_protokoll AS vp ON vp.versand_id = v.id
            JOIN protokoll AS p ON p.id = vp.protokoll_id
            WHERE p.id = %s
            ''',
            (manual_id,),
        )
        self.assertEqual(row[0], 'MANUAL')
        self.assertEqual(row[1], 'MANUALLY_SENT')
        self.assertEqual(row[2], 'manual')
        self.assertEqual(row[3], 'Utilisateur HOME')
        self.assertEqual(row[4], 'Versand urgent synthétique')
        self.assertEqual(row[5], manual_at.replace(tzinfo=None))
        self.assertEqual(row[6], 'MANUALLY_SENT')
        self.assertEqual(row[7], calculate_sha256(manual_pdf))
        snapshot = json.loads(row[8])
        self.assertTrue(snapshot)
        self.assertTrue(
            all(
                item['email'].endswith('@example.invalid')
                for item in snapshot
            )
        )
        self.connection.commit()
        weekly_at = datetime(
            2026,
            8,
            7,
            16,
            0,
            tzinfo=timezone.utc,
        )

        first_weekly = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=weekly_at,
        )

        self.assertTrue(first_weekly.weekly_due)
        self.assertEqual(first_weekly.ready_document_count, 1)
        self.assertEqual(len(first_weekly.messages), 1)
        self.assertEqual(len(list(outbox.glob('*.eml'))), eml_before + 1)
        message = BytesParser(policy=policy.default).parsebytes(
            first_weekly.messages[0].message_path.read_bytes()
        )
        attachment_names = [
            part.get_filename() for part in message.iter_attachments()
        ]
        self.assertEqual(attachment_names, [automatic_pdf.name])
        self.assertNotIn(manual_pdf.name, attachment_names)
        self.assertEqual(
            self._row(
                '''
                SELECT
                    MAX(CASE WHEN dateiname = %s THEN status END),
                    MAX(CASE WHEN dateiname = %s THEN status END)
                FROM protokoll
                ''',
                (manual_pdf.name, automatic_pdf.name),
            ),
            ('MANUALLY_SENT', 'ACCEPTED'),
        )
        self.connection.commit()
        versand_after_first = int(
            self._scalar('SELECT COUNT(*) FROM versand')
        )
        eml_after_first = len(list(outbox.glob('*.eml')))
        self.connection.commit()

        second_weekly = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=weekly_at,
        )

        self.assertFalse(second_weekly.weekly_due)
        self.assertEqual(second_weekly.messages, ())
        self.assertEqual(
            int(self._scalar('SELECT COUNT(*) FROM versand')),
            versand_after_first,
        )
        self.assertEqual(len(list(outbox.glob('*.eml'))), eml_after_first)

    def test_manual_urgent_versand_rejects_changed_sha256(self) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / '00125083_URGENT_SHA_CHANGED.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic urgent initial'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            database=self.settings,
        )
        scan_and_persist(self.connection, root, PATTERN)
        scan_and_persist(self.connection, root, PATTERN)
        protokoll_id = int(self._scalar('SELECT id FROM protokoll'))
        self.connection.commit()
        pdf.write_bytes(build_pdf_bytes('synthetic urgent changed'))

        with self.assertRaisesRegex(ManualVersandError, 'SHA-256'):
            record_manual_urgent_versand(
                self.connection,
                settings,
                protokoll_id,
                'Utilisateur HOME',
                'Versand urgent synthétique',
            )

        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 0)
        self.assertEqual(
            self._scalar('SELECT status FROM protokoll'),
            'READY',
        )

    def test_same_csv_is_imported_only_once(self) -> None:
        first = import_baustellen_csv(self.connection, SAMPLE_CSV)
        second = import_baustellen_csv(self.connection, SAMPLE_CSV)

        self.assertFalse(first.duplicate_import)
        self.assertEqual(first.inserted_auftraege, 2)
        self.assertTrue(second.duplicate_import)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM auftrag'), 2)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM empfaenger'), 3)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM csv_import'), 1)
        self.assertEqual(
            self._scalar(
                '''
                SELECT auftragsnummer
                FROM auftrag
                WHERE auftragsnummer = %s
                ''',
                ('00125083',),
            ),
            '00125083',
        )

    def test_invalid_csv_rolls_back_completely(self) -> None:
        invalid = self._write_csv(
            HEADER
            + '0099,Testbau,Test GmbH,Anna,'
            'adresse-invalide,TO,1\n'
        )

        with self.assertRaises(CsvImportRejected):
            import_baustellen_csv(self.connection, invalid)

        self.assertEqual(self._scalar('SELECT COUNT(*) FROM baustelle'), 0)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM auftrag'), 0)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM empfaenger'), 0)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM csv_import'), 0)

    def test_absent_recipient_is_not_deleted_or_deactivated(self) -> None:
        original = self._write_csv(
            HEADER
            + '0099,Testbau,Test GmbH,Anna,'
            'anna@example.invalid,TO,1\n'
            + '0099,Testbau,Test GmbH,Ben,'
            'ben@example.invalid,CC,1\n'
        )
        reduced = self._write_csv(
            HEADER
            + '0099,Testbau,Test GmbH,Anna,'
            'anna@example.invalid,TO,1\n'
        )

        import_baustellen_csv(self.connection, original)
        import_baustellen_csv(self.connection, reduced)

        self.assertEqual(self._scalar('SELECT COUNT(*) FROM empfaenger'), 2)
        self.assertEqual(
            self._scalar(
                '''
                SELECT aktiv
                FROM empfaenger
                WHERE email = 'ben@example.invalid'
                '''
            ),
            1,
        )

    def test_changed_csv_preserves_omitted_auftrag_and_is_idempotent(
        self,
    ) -> None:
        first_csv = self._write_csv(
            HEADER
            + '00991001,Baustelle Eins,Kunde HOME,Anna Alt,'
            'anna.991001@example.invalid,TO,1\n'
            + '00991002,Baustelle Zwei,Kunde HOME,Ben Bestand,'
            'ben.991002@example.invalid,TO,1\n'
            + '00991002,Baustelle Zwei,Kunde HOME,Clara Bestand,'
            'clara.991002@example.invalid,CC,1\n'
        )
        second_csv = self._write_csv(
            HEADER
            + '00991001,Baustelle Eins Aktualisiert,Kunde HOME,'
            'Anna Neu,anna.991001@example.invalid,TO,1\n'
            + '00991003,Baustelle Drei,Kunde HOME,Dora Neu,'
            'dora.991003@example.invalid,TO,1\n'
        )

        first = import_baustellen_csv(self.connection, first_csv)

        self.assertFalse(first.duplicate_import)
        self.assertEqual(first.inserted_auftraege, 2)
        self.assertEqual(first.updated_auftraege, 0)
        self.assertEqual(first.deleted_auftraege, 0)
        self.assertEqual(first.deactivated_auftraege, 0)
        self.assertEqual(
            self._row(
                '''
                SELECT COUNT(*), SUM(aktiv)
                FROM auftrag
                WHERE auftragsnummer IN ('00991001', '00991002')
                '''
            ),
            (2, 2),
        )
        omitted_before = self._row(
            '''
            SELECT
                a.id,
                a.aktiv,
                b.name,
                COUNT(e.id),
                SUM(e.aktiv),
                GROUP_CONCAT(e.email ORDER BY e.email SEPARATOR '|')
            FROM auftrag AS a
            JOIN baustelle AS b ON b.id = a.baustelle_id
            JOIN empfaenger AS e ON e.auftrag_id = a.id
            WHERE a.auftragsnummer = '00991002'
            GROUP BY a.id, a.aktiv, b.name
            '''
        )
        self.connection.commit()

        second = import_baustellen_csv(self.connection, second_csv)

        self.assertFalse(second.duplicate_import)
        self.assertNotEqual(first.csv_sha256, second.csv_sha256)
        self.assertEqual(second.inserted_auftraege, 1)
        self.assertEqual(second.updated_auftraege, 1)
        self.assertEqual(second.deleted_auftraege, 0)
        self.assertEqual(second.deactivated_auftraege, 0)
        self.assertEqual(
            self._row(
                '''
                SELECT
                    COUNT(*),
                    MIN(a.aktiv),
                    MAX(b.name),
                    MAX(e.kontakt_name)
                FROM auftrag AS a
                JOIN baustelle AS b ON b.id = a.baustelle_id
                JOIN empfaenger AS e ON e.auftrag_id = a.id
                WHERE a.auftragsnummer = '00991001'
                '''
            ),
            (1, 1, 'Baustelle Eins Aktualisiert', 'Anna Neu'),
        )
        self.assertEqual(
            self._row(
                '''
                SELECT
                    a.id,
                    a.aktiv,
                    b.name,
                    COUNT(e.id),
                    SUM(e.aktiv),
                    GROUP_CONCAT(e.email ORDER BY e.email SEPARATOR '|')
                FROM auftrag AS a
                JOIN baustelle AS b ON b.id = a.baustelle_id
                JOIN empfaenger AS e ON e.auftrag_id = a.id
                WHERE a.auftragsnummer = '00991002'
                GROUP BY a.id, a.aktiv, b.name
                '''
            ),
            omitted_before,
        )
        self.assertEqual(
            self._row(
                '''
                SELECT COUNT(*), MIN(a.aktiv), MAX(b.name)
                FROM auftrag AS a
                JOIN baustelle AS b ON b.id = a.baustelle_id
                WHERE a.auftragsnummer = '00991003'
                '''
            ),
            (1, 1, 'Baustelle Drei'),
        )
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM auftrag'), 3)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM empfaenger'), 4)
        self.connection.commit()

        duplicate = import_baustellen_csv(self.connection, second_csv)

        self.assertTrue(duplicate.duplicate_import)
        self.assertEqual(duplicate.csv_sha256, second.csv_sha256)
        self.assertEqual(duplicate.inserted_auftraege, 0)
        self.assertEqual(duplicate.updated_auftraege, 0)
        self.assertEqual(duplicate.deleted_auftraege, 0)
        self.assertEqual(duplicate.deactivated_auftraege, 0)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM auftrag'), 3)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM empfaenger'), 4)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM csv_import'), 2)

    def test_explicit_inactive_auftrag_blocks_scan_and_versand(
        self,
    ) -> None:
        initial_csv = self._write_csv(
            HEADER
            + '00991001,Baustelle Eins,Kunde HOME,Anna,'
            'anna.991001@example.invalid,TO,1\n'
            + '00991002,Baustelle Zwei,Kunde HOME,Ben,'
            'ben.991002@example.invalid,TO,1\n'
            + '00991003,Baustelle Drei,Kunde HOME,Dora,'
            'dora.991003@example.invalid,TO,1\n'
        )
        inactive_csv = self._write_csv(
            HEADER
            + '00991003,Baustelle Drei,Kunde HOME,Dora,'
            'dora.991003@example.invalid,TO,0\n'
        )
        import_baustellen_csv(self.connection, initial_csv)
        unchanged_before = self._row(
            '''
            SELECT
                GROUP_CONCAT(
                    CONCAT_WS(
                        ':',
                        a.id,
                        a.auftragsnummer,
                        a.aktiv,
                        b.id,
                        b.aktiv,
                        e.id,
                        e.email,
                        e.aktiv
                    )
                    ORDER BY a.auftragsnummer
                    SEPARATOR '|'
                )
            FROM auftrag AS a
            JOIN baustelle AS b ON b.id = a.baustelle_id
            JOIN empfaenger AS e ON e.auftrag_id = a.id
            WHERE a.auftragsnummer IN ('00991001', '00991002')
            '''
        )
        target_before = self._row(
            '''
            SELECT a.id, a.aktiv, b.id, b.aktiv, e.id, e.aktiv
            FROM auftrag AS a
            JOIN baustelle AS b ON b.id = a.baustelle_id
            JOIN empfaenger AS e ON e.auftrag_id = a.id
            WHERE a.auftragsnummer = '00991003'
            '''
        )
        self.connection.commit()

        deactivation = import_baustellen_csv(
            self.connection,
            inactive_csv,
        )

        self.assertFalse(deactivation.duplicate_import)
        self.assertEqual(deactivation.inserted_auftraege, 0)
        self.assertEqual(deactivation.updated_auftraege, 1)
        self.assertEqual(deactivation.deleted_auftraege, 0)
        self.assertEqual(deactivation.deactivated_auftraege, 1)
        target_after = self._row(
            '''
            SELECT a.id, a.aktiv, b.id, b.aktiv, e.id, e.aktiv
            FROM auftrag AS a
            JOIN baustelle AS b ON b.id = a.baustelle_id
            JOIN empfaenger AS e ON e.auftrag_id = a.id
            WHERE a.auftragsnummer = '00991003'
            '''
        )
        self.assertEqual(
            target_after,
            (
                target_before[0],
                0,
                target_before[2],
                0,
                target_before[4],
                0,
            ),
        )
        self.assertEqual(
            self._row(
                '''
                SELECT
                    GROUP_CONCAT(
                        CONCAT_WS(
                            ':',
                            a.id,
                            a.auftragsnummer,
                            a.aktiv,
                            b.id,
                            b.aktiv,
                            e.id,
                            e.email,
                            e.aktiv
                        )
                        ORDER BY a.auftragsnummer
                        SEPARATOR '|'
                    )
                FROM auftrag AS a
                JOIN baustelle AS b ON b.id = a.baustelle_id
                JOIN empfaenger AS e ON e.auftrag_id = a.id
                WHERE a.auftragsnummer IN ('00991001', '00991002')
                '''
            ),
            unchanged_before,
        )
        self.connection.commit()

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        pdf = root / '00991003_INACTIVE_HOME.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic inactive Auftrag'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )

        first_scan = scan_and_persist(self.connection, root, PATTERN)
        second_scan = scan_and_persist(self.connection, root, PATTERN)

        self.assertEqual(
            first_scan.report.items[0].status,
            ProtokollStatus.DISCOVERED,
        )
        self.assertEqual(
            second_scan.report.items[0].status,
            ProtokollStatus.MANUAL_REVIEW,
        )
        self.assertEqual(
            second_scan.report.items[0].reason,
            'Auftrag inconnu ou inactif',
        )
        self.assertEqual(
            self._row(
                '''
                SELECT p.status, p.letzte_fehler, p.auftrag_id
                FROM protokoll AS p
                WHERE p.dateiname = '00991003_INACTIVE_HOME.pdf'
                '''
            ),
            ('MANUAL_REVIEW', 'Auftrag inconnu ou inactif', None),
        )
        pending = list_manual_reviews(self.connection, settings)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].filename, pdf.name)
        self.assertEqual(
            pending[0].reason,
            'Auftrag inconnu ou inactif',
        )
        simulated_at = datetime(
            2026,
            8,
            21,
            16,
            0,
            tzinfo=timezone.utc,
        )
        first_versand = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )
        self.assertEqual(first_versand.ready_document_count, 0)
        self.assertEqual(first_versand.provider_call_count, 0)
        self.assertEqual(first_versand.messages, ())
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 0)
        self.assertEqual(len(list(outbox.glob('*.eml'))), 0)

        third_scan = scan_and_persist(self.connection, root, PATTERN)
        second_versand = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )

        self.assertEqual(
            third_scan.report.items[0].status,
            ProtokollStatus.MANUAL_REVIEW,
        )
        self.assertEqual(
            third_scan.report.items[0].reason,
            'Auftrag inconnu ou inactif',
        )
        self.assertEqual(second_versand.provider_call_count, 0)
        self.assertEqual(second_versand.messages, ())
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 0)
        self.assertEqual(len(list(outbox.glob('*.eml'))), 0)
        self.connection.commit()

        duplicate = import_baustellen_csv(
            self.connection,
            inactive_csv,
        )

        self.assertTrue(duplicate.duplicate_import)
        self.assertEqual(
            duplicate.csv_sha256,
            deactivation.csv_sha256,
        )
        self.assertEqual(duplicate.inserted_auftraege, 0)
        self.assertEqual(duplicate.updated_auftraege, 0)
        self.assertEqual(
            self._row(
                '''
                SELECT a.aktiv, b.aktiv, e.aktiv
                FROM auftrag AS a
                JOIN baustelle AS b ON b.id = a.baustelle_id
                JOIN empfaenger AS e ON e.auftrag_id = a.id
                WHERE a.auftragsnummer = '00991003'
                '''
            ),
            (0, 0, 0),
        )

    def test_named_locks_skip_concurrent_scan_and_versand(self) -> None:
        csv_path = self._write_csv(
            HEADER
            + '00991001,Baustelle Concurrente,Kunde HOME,Anna,'
            'anna.991001@example.invalid,TO,1\n'
        )
        import_baustellen_csv(self.connection, csv_path)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        pdf = root / '00991001_CONCURRENT_HOME.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic concurrent locks'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )
        concurrent = connect_mysql(self.settings)
        self.addCleanup(concurrent.close)

        cursor = self.connection.cursor()
        cursor.execute(
            'SELECT GET_LOCK(%s, 0)',
            ('pruefversand:nas-scan',),
        )
        self.assertEqual(cursor.fetchone()[0], 1)
        self.connection.commit()
        cursor.close()

        skipped_scan = scan_and_persist(
            concurrent,
            root,
            PATTERN,
        )

        self.assertFalse(skipped_scan.lock_acquired)
        self.assertTrue(skipped_scan.skipped_due_to_lock)
        self.assertEqual(skipped_scan.report.items, ())
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM protokoll'), 0)
        self.connection.commit()
        cursor = self.connection.cursor()
        cursor.execute(
            'SELECT RELEASE_LOCK(%s)',
            ('pruefversand:nas-scan',),
        )
        self.assertEqual(cursor.fetchone()[0], 1)
        self.connection.commit()
        cursor.close()

        first_scan = scan_and_persist(concurrent, root, PATTERN)
        second_scan = scan_and_persist(concurrent, root, PATTERN)

        self.assertEqual(
            first_scan.report.items[0].status,
            ProtokollStatus.DISCOVERED,
        )
        self.assertEqual(
            second_scan.report.items[0].status,
            ProtokollStatus.READY,
        )
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM protokoll'), 1)
        self.assertEqual(
            self._scalar('SELECT COUNT(DISTINCT sha256) FROM protokoll'),
            1,
        )
        self.connection.commit()

        cursor = self.connection.cursor()
        cursor.execute(
            'SELECT GET_LOCK(%s, 0)',
            ('pruefversand:mock-versand',),
        )
        self.assertEqual(cursor.fetchone()[0], 1)
        self.connection.commit()
        cursor.close()
        simulated_at = datetime(
            2026,
            8,
            28,
            16,
            0,
            tzinfo=timezone.utc,
        )

        skipped_versand = prepare_ready_mock_versand(
            concurrent,
            settings,
            run_at=simulated_at,
        )

        self.assertFalse(skipped_versand.lock_acquired)
        self.assertTrue(skipped_versand.skipped_due_to_lock)
        self.assertEqual(skipped_versand.provider_call_count, 0)
        self.assertEqual(skipped_versand.messages, ())
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 0)
        self.assertEqual(len(list(outbox.glob('*.eml'))), 0)
        self.connection.commit()
        cursor = self.connection.cursor()
        cursor.execute(
            'SELECT RELEASE_LOCK(%s)',
            ('pruefversand:mock-versand',),
        )
        self.assertEqual(cursor.fetchone()[0], 1)
        self.connection.commit()
        cursor.close()

        sent = prepare_ready_mock_versand(
            concurrent,
            settings,
            run_at=simulated_at,
        )

        self.assertTrue(sent.lock_acquired)
        self.assertFalse(sent.skipped_due_to_lock)
        self.assertEqual(sent.provider_call_count, 1)
        self.assertEqual(len(sent.messages), 1)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 1)
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand_protokoll'),
            1,
        )
        self.assertEqual(len(list(outbox.glob('*.eml'))), 1)
        self.connection.commit()
        self.assertEqual(
            self._row(
                '''
                SELECT
                    IS_FREE_LOCK('pruefversand:nas-scan'),
                    IS_FREE_LOCK('pruefversand:mock-versand')
                '''
            ),
            (1, 1),
        )

    def test_scanner_persists_discovered_then_ready(self) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / '00125083_F001.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic stable'))

        first = scan_and_persist(self.connection, root, PATTERN)

        self.assertEqual(
            first.report.items[0].status,
            ProtokollStatus.DISCOVERED,
        )
        self.assertEqual(first.inserted_protokolle, 1)
        self.assertEqual(
            self._row(
                '''
                SELECT status, stable_scan_count
                FROM scan_observation
                '''
            ),
            ('DISCOVERED', 1),
        )

        second = scan_and_persist(self.connection, root, PATTERN)

        self.assertEqual(
            second.report.items[0].status,
            ProtokollStatus.READY,
        )
        self.assertEqual(second.updated_protokolle, 1)
        self.assertEqual(
            self._row(
                '''
                SELECT status, stable_scan_count
                FROM scan_observation
                '''
            ),
            ('READY', 2),
        )
        self.assertEqual(
            self._row(
                '''
                SELECT p.status, p.stable_scan_count, a.auftragsnummer
                FROM protokoll AS p
                JOIN auftrag AS a ON a.id = p.auftrag_id
                '''
            ),
            ('READY', 2, '00125083'),
        )

    def test_modified_pdf_restarts_stability_counter(self) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / '00125083_F001.pdf'
        pdf.write_bytes(build_pdf_bytes('version one'))
        scan_and_persist(self.connection, root, PATTERN)
        pdf.write_bytes(build_pdf_bytes('version two is longer'))

        second = scan_and_persist(self.connection, root, PATTERN)

        self.assertEqual(
            second.report.items[0].status,
            ProtokollStatus.DISCOVERED,
        )
        self.assertEqual(
            self._row(
                '''
                SELECT status, stable_scan_count
                FROM scan_observation
                '''
            ),
            ('DISCOVERED', 1),
        )

    def test_cli_import_then_two_persistent_scans(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / '00125083_F001.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic cli'))
        config = self._write_home_config(root)

        import_output = StringIO()
        with redirect_stdout(import_output):
            import_code = main(
                [
                    '--config',
                    str(config),
                    'import-csv',
                    str(SAMPLE_CSV),
                ]
            )
        first_output = StringIO()
        with redirect_stdout(first_output):
            first_code = main(['--config', str(config), 'scan'])
        second_output = StringIO()
        with redirect_stdout(second_output):
            second_code = main(['--config', str(config), 'scan'])

        self.assertEqual((import_code, first_code, second_code), (0, 0, 0))
        self.assertEqual(
            json.loads(import_output.getvalue())['status'],
            'IMPORTED',
        )
        self.assertEqual(
            json.loads(first_output.getvalue())['files'][0]['status'],
            'DISCOVERED',
        )
        second_payload = json.loads(second_output.getvalue())
        self.assertEqual(second_payload['files'][0]['status'], 'READY')
        self.assertTrue(second_payload['dry_run'])
        self.assertEqual(second_payload['mail_provider'], 'mock')

    def test_unknown_auftrag_never_becomes_ready(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / '999999_F001.pdf'
        pdf.write_bytes(build_pdf_bytes('unknown Auftrag'))

        scan_and_persist(self.connection, root, PATTERN)
        second = scan_and_persist(self.connection, root, PATTERN)

        self.assertEqual(
            second.report.items[0].status,
            ProtokollStatus.MANUAL_REVIEW,
        )
        self.assertEqual(
            self._scalar('SELECT status FROM protokoll'),
            'MANUAL_REVIEW',
        )

    def test_manual_review_assign_preserves_leading_zero_and_no_versand(
        self,
    ) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / '999999_F001.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic manual assignment'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            database=self.settings,
        )
        scan_and_persist(self.connection, root, PATTERN)
        scan_and_persist(self.connection, root, PATTERN)

        pending = list_manual_reviews(self.connection, settings)

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].filename, pdf.name)
        self.assertIn('Auftrag inconnu', pending[0].reason)
        result = assign_manual_review(
            self.connection,
            settings,
            pending[0].protokoll_id,
            '00125083',
            'Association HOME synthetique verifiee',
        )

        self.assertEqual(result.action, 'ASSIGN')
        self.assertEqual(result.auftragsnummer, '00125083')
        self.assertEqual(result.protokoll_status, 'READY')
        self.assertEqual(
            self._row(
                '''
                SELECT
                    p.status,
                    a.auftragsnummer,
                    d.action,
                    d.reason,
                    d.decided_at IS NOT NULL
                FROM protokoll AS p
                JOIN auftrag AS a ON a.id = p.auftrag_id
                JOIN manual_review_decision AS d
                    ON d.protokoll_id = p.id
                '''
            ),
            (
                'READY',
                '00125083',
                'ASSIGN',
                'Association HOME synthetique verifiee',
                1,
            ),
        )
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*)
                FROM audit_event
                WHERE event_type = 'MANUAL_REVIEW_ASSIGN'
                '''
            ),
            1,
        )
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 0)
        self.assertTrue(pdf.is_file())
        self.assertEqual(list_manual_reviews(self.connection, settings), ())

        third = scan_and_persist(self.connection, root, PATTERN)

        self.assertEqual(
            third.report.items[0].status,
            ProtokollStatus.READY,
        )
        self.assertEqual(
            third.report.items[0].auftragsnummer,
            '00125083',
        )
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 0)

    def test_manually_assigned_pdf_is_sent_once_in_next_mock_versand(
        self,
    ) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / '999999_MANUAL_Versand.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic assigned Versand'))
        outbox = root / 'outbox'
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )
        scan_and_persist(self.connection, root, PATTERN)
        scan_and_persist(self.connection, root, PATTERN)
        pending = list_manual_reviews(self.connection, settings)
        assign_manual_review(
            self.connection,
            settings,
            pending[0].protokoll_id,
            '00125083',
            'Cycle Versand HOME synthetique',
        )

        first = prepare_ready_mock_versand(self.connection, settings)

        self.assertEqual(first.ready_document_count, 1)
        self.assertEqual(first.already_processed_count, 0)
        self.assertEqual(len(first.messages), 1)
        message_path = first.messages[0].message_path
        self.assertTrue(message_path.is_file())
        message = BytesParser(policy=policy.default).parsebytes(
            message_path.read_bytes()
        )
        addresses = [
            address
            for _, address in getaddresses(
                [str(message['To'] or ''), str(message['Cc'] or '')]
            )
            if address
        ]
        self.assertTrue(addresses)
        self.assertTrue(
            all(
                address.endswith('@example.invalid')
                for address in addresses
            )
        )
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), pdf.name)
        self.assertEqual(
            attachments[0].get_payload(decode=True),
            pdf.read_bytes(),
        )
        self.assertEqual(
            self._row(
                '''
                SELECT
                    p.status,
                    d.action,
                    a.auftragsnummer
                FROM protokoll AS p
                JOIN manual_review_decision AS d
                    ON d.protokoll_id = p.id
                JOIN auftrag AS a ON a.id = p.auftrag_id
                '''
            ),
            ('ACCEPTED', 'ASSIGN', '00125083'),
        )
        self.connection.commit()
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 1)
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand_protokoll'),
            1,
        )
        self.connection.commit()
        eml_count = len(list(outbox.glob('*.eml')))

        second = prepare_ready_mock_versand(self.connection, settings)

        self.assertEqual(second.ready_document_count, 0)
        self.assertEqual(second.already_processed_count, 1)
        self.assertEqual(second.messages, ())
        self.assertEqual(len(list(outbox.glob('*.eml'))), eml_count)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 1)
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand_protokoll'),
            1,
        )

    def test_weekly_versand_groups_two_new_pdfs_and_runs_once(self) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )

        old_pdf = root / 'F0661.pdf'
        old_pdf.write_bytes(build_pdf_bytes('synthetic old accepted'))
        scan_and_persist(self.connection, root, PATTERN)
        scan_and_persist(self.connection, root, PATTERN)
        old_pending = list_manual_reviews(self.connection, settings)
        assign_manual_review(
            self.connection,
            settings,
            old_pending[0].protokoll_id,
            '00125083',
            'Ancien PDF synthetique',
        )
        old_preparation = prepare_ready_mock_versand(
            self.connection,
            settings,
        )
        self.assertEqual(len(old_preparation.messages), 1)
        old_eml_count = len(list(outbox.glob('*.eml')))

        first_pdf = root / '00125083_WOCHE_A.pdf'
        second_pdf = root / '00125083_WOCHE_B.pdf'
        first_pdf.write_bytes(build_pdf_bytes('synthetic weekly A'))
        second_pdf.write_bytes(build_pdf_bytes('synthetic weekly B'))
        first_scan = scan_and_persist(self.connection, root, PATTERN)
        second_scan = scan_and_persist(self.connection, root, PATTERN)
        new_names = {first_pdf.name, second_pdf.name}
        self.assertEqual(
            {
                item.path.name: item.status
                for item in first_scan.report.items
                if item.path.name in new_names
            },
            {
                first_pdf.name: ProtokollStatus.DISCOVERED,
                second_pdf.name: ProtokollStatus.DISCOVERED,
            },
        )
        self.assertEqual(
            {
                item.path.name: item.status
                for item in second_scan.report.items
                if item.path.name in new_names
            },
            {
                first_pdf.name: ProtokollStatus.READY,
                second_pdf.name: ProtokollStatus.READY,
            },
        )
        simulated_at = datetime(
            2026,
            7,
            31,
            16,
            0,
            tzinfo=timezone.utc,
        )

        first_weekly = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )

        self.assertTrue(first_weekly.weekly_due)
        self.assertEqual(first_weekly.period_start, date(2026, 7, 27))
        self.assertEqual(first_weekly.ready_document_count, 2)
        self.assertEqual(first_weekly.already_processed_count, 1)
        self.assertEqual(len(first_weekly.messages), 1)
        weekly_message_path = first_weekly.messages[0].message_path
        weekly_message = BytesParser(policy=policy.default).parsebytes(
            weekly_message_path.read_bytes()
        )
        attachment_names = {
            part.get_filename()
            for part in weekly_message.iter_attachments()
        }
        self.assertEqual(attachment_names, new_names)
        self.assertNotIn(old_pdf.name, attachment_names)
        recipients = [
            address
            for _, address in getaddresses(
                [
                    str(weekly_message['To'] or ''),
                    str(weekly_message['Cc'] or ''),
                ]
            )
            if address
        ]
        self.assertTrue(recipients)
        self.assertTrue(
            all(
                recipient.endswith('@example.invalid')
                for recipient in recipients
            )
        )
        self.assertEqual(
            self._row(
                '''
                SELECT COUNT(*), MIN(period_start), MAX(period_start)
                FROM versand
                WHERE period_start = '2026-07-27'
                '''
            ),
            (1, date(2026, 7, 27), date(2026, 7, 27)),
        )
        self.connection.commit()
        eml_after_first = len(list(outbox.glob('*.eml')))
        self.assertEqual(eml_after_first, old_eml_count + 1)

        second_weekly = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )

        self.assertFalse(second_weekly.weekly_due)
        self.assertEqual(second_weekly.ready_document_count, 0)
        self.assertEqual(second_weekly.already_processed_count, 3)
        self.assertEqual(second_weekly.messages, ())
        self.assertEqual(len(list(outbox.glob('*.eml'))), eml_after_first)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 2)
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand_protokoll'),
            3,
        )

    def _prepare_attachment_limit_case(
        self,
        *,
        limit_delta: int,
    ) -> tuple[object, Path, tuple[Path, Path], int, int, datetime]:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        first_pdf = root / '00125083_LIMIT_A.pdf'
        second_pdf = root / '00125083_LIMIT_B.pdf'
        first_pdf.write_bytes(build_pdf_bytes('synthetic attachment A'))
        second_pdf.write_bytes(build_pdf_bytes('synthetic attachment B'))
        total_bytes = (
            first_pdf.stat().st_size + second_pdf.stat().st_size
        )
        limit_bytes = total_bytes + limit_delta
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
                maximum_attachment_bytes=limit_bytes,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )
        scan_and_persist(self.connection, root, PATTERN)
        second_scan = scan_and_persist(self.connection, root, PATTERN)
        self.assertEqual(
            {item.status for item in second_scan.report.items},
            {ProtokollStatus.READY},
        )
        simulated_at = datetime(
            2026,
            9,
            11,
            16,
            0,
            tzinfo=timezone.utc,
        )
        return (
            settings,
            outbox,
            (first_pdf, second_pdf),
            total_bytes,
            limit_bytes,
            simulated_at,
        )

    def test_attachment_total_exactly_at_limit_is_accepted(self) -> None:
        (
            settings,
            outbox,
            pdfs,
            total_bytes,
            limit_bytes,
            simulated_at,
        ) = self._prepare_attachment_limit_case(limit_delta=0)

        result = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )

        self.assertEqual(total_bytes, limit_bytes)
        self.assertTrue(all(pdf.stat().st_size < limit_bytes for pdf in pdfs))
        self.assertEqual(result.provider_call_count, 1)
        self.assertEqual(len(result.messages), 1)
        self.assertEqual(result.manual_review_incidents, ())
        self.assertEqual(len(list(outbox.glob('*.eml'))), 1)
        self.assertEqual(
            self._row(
                '''
                SELECT COUNT(*), MIN(status), MAX(status)
                FROM protokoll
                '''
            ),
            (2, 'ACCEPTED', 'ACCEPTED'),
        )
        self.assertEqual(
            self._row(
                'SELECT status, versuch_anzahl FROM versand'
            ),
            ('ACCEPTED', 1),
        )

    def test_attachment_total_one_byte_over_is_blocked_once(self) -> None:
        (
            settings,
            outbox,
            pdfs,
            total_bytes,
            limit_bytes,
            simulated_at,
        ) = self._prepare_attachment_limit_case(limit_delta=-1)
        self.assertEqual(total_bytes, limit_bytes + 1)
        self.assertTrue(all(pdf.stat().st_size < limit_bytes for pdf in pdfs))

        first = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )

        self.assertEqual(first.provider_call_count, 0)
        self.assertEqual(first.messages, ())
        self.assertEqual(first.blocked_auftraege, ('00125083',))
        self.assertEqual(len(first.manual_review_incidents), 1)
        incident = first.manual_review_incidents[0]
        self.assertEqual(
            incident.incident_type,
            'ATTACHMENT_LIMIT_EXCEEDED',
        )
        self.assertEqual(incident.total_attachment_bytes, total_bytes)
        self.assertEqual(incident.maximum_attachment_bytes, limit_bytes)
        self.assertFalse(incident.provider_invoked)
        self.assertTrue(incident.automatic_retry_blocked)
        self.assertFalse(outbox.exists())
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 1)
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand_protokoll'),
            2,
        )
        self.assertEqual(
            self._row(
                '''
                SELECT status, versuch_anzahl, letzte_fehler
                FROM versand
                '''
            )[:2],
            ('MANUAL_REVIEW', 0),
        )
        reasons = self._row(
            '''
            SELECT MIN(letzte_fehler), MAX(letzte_fehler)
            FROM protokoll
            WHERE status = 'MANUAL_REVIEW'
            '''
        )
        self.assertEqual(reasons[0], reasons[1])
        self.assertIn(f'total={total_bytes} octets', str(reasons[0]))
        self.assertIn(f'limite={limit_bytes} octets', str(reasons[0]))
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*) FROM audit_event
                WHERE event_type = 'MOCK_PROVIDER_CALL_STARTED'
                '''
            ),
            0,
        )
        audit_type, raw_details = self._row(
            'SELECT event_type, details FROM audit_event'
        )
        details = (
            json.loads(raw_details)
            if isinstance(raw_details, str)
            else raw_details
        )
        self.assertEqual(audit_type, 'ATTACHMENT_LIMIT_EXCEEDED')
        self.assertEqual(details['total_attachment_bytes'], total_bytes)
        self.assertEqual(details['maximum_attachment_bytes'], limit_bytes)
        self.assertFalse(details['provider_invoked'])
        self.assertFalse(details['message_accepted'])
        self.assertFalse(details['eml_created'])
        self.assertFalse(details['split_performed'])
        self.assertFalse(details['automatic_retry'])

        self.connection.rollback()
        reviews = list_versand_review_incidents(
            self.connection,
            settings,
        )
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].versand_id, incident.versand_id)
        self.assertEqual(reviews[0].linked_document_count, 2)
        self.assertTrue(reviews[0].automatic_retry_blocked)
        self.assertEqual(list_manual_reviews(self.connection, settings), ())
        with self.assertRaisesRegex(
            VersandResolutionError,
            'aucun appel fournisseur',
        ):
            confirm_provider_accepted(
                self.connection,
                settings,
                int(incident.versand_id),
                'Interdit sans appel fournisseur',
            )
        with self.assertRaisesRegex(
            VersandResolutionError,
            'aucun appel fournisseur initial',
        ):
            retry_after_provider_not_accepted(
                self.connection,
                settings,
                int(incident.versand_id),
                'Interdit sans appel fournisseur',
            )

        versand_count = self._scalar('SELECT COUNT(*) FROM versand')
        audit_count = self._scalar('SELECT COUNT(*) FROM audit_event')
        second = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )
        restored_settings = replace(
            settings,
            app=replace(
                settings.app,
                maximum_attachment_bytes=20_000_000,
            ),
        )
        restored = prepare_ready_mock_versand(
            self.connection,
            restored_settings,
            run_at=simulated_at,
        )

        self.assertEqual(second.ready_document_count, 0)
        self.assertEqual(second.provider_call_count, 0)
        self.assertEqual(second.messages, ())
        self.assertEqual(restored.ready_document_count, 0)
        self.assertEqual(restored.provider_call_count, 0)
        self.assertEqual(restored.messages, ())
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand'),
            versand_count,
        )
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM audit_event'),
            audit_count,
        )
        self.assertFalse(outbox.exists())

    def test_ambiguous_mock_timeout_requires_manual_review_without_retry(
        self,
    ) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        pdf = root / '00125083_TIMEOUT_AMBIGU.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic ambiguous timeout'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )
        first_scan = scan_and_persist(self.connection, root, PATTERN)
        second_scan = scan_and_persist(self.connection, root, PATTERN)
        self.assertEqual(
            first_scan.report.items[0].status,
            ProtokollStatus.DISCOVERED,
        )
        self.assertEqual(
            second_scan.report.items[0].status,
            ProtokollStatus.READY,
        )
        versand_before = self._scalar('SELECT COUNT(*) FROM versand')
        audit_before = self._scalar('SELECT COUNT(*) FROM audit_event')
        self.connection.commit()

        first = prepare_ready_mock_versand(
            self.connection,
            settings,
            simulate_timeout=True,
        )

        self.assertEqual(first.ready_document_count, 1)
        self.assertEqual(first.messages, ())
        self.assertEqual(len(first.manual_review_incidents), 1)
        incident = first.manual_review_incidents[0]
        self.assertEqual(incident.auftragsnummer, '00125083')
        self.assertIn('timeout ambigu', incident.reason)
        self.assertEqual(len(list(outbox.glob('*.eml'))), 0)
        row = self._row(
            '''
            SELECT
                v.status,
                v.letzte_fehler,
                v.versuch_anzahl,
                v.accepted_at,
                p.status,
                p.letzte_fehler
            FROM versand AS v
            JOIN versand_protokoll AS vp ON vp.versand_id = v.id
            JOIN protokoll AS p ON p.id = vp.protokoll_id
            WHERE p.dateiname = '00125083_TIMEOUT_AMBIGU.pdf'
            '''
        )
        self.assertEqual(row[0], 'MANUAL_REVIEW')
        self.assertIn('timeout ambigu', row[1])
        self.assertEqual(row[2], 1)
        self.assertIsNone(row[3])
        self.assertEqual(row[4], 'MANUAL_REVIEW')
        self.assertEqual(row[5], row[1])
        self.connection.commit()
        pending = list_manual_reviews(self.connection, settings)
        self.assertEqual(pending, ())
        read_only_snapshot = self._row(
            '''
            SELECT
                (SELECT COUNT(*) FROM versand),
                (SELECT COUNT(*) FROM protokoll),
                (SELECT COUNT(*) FROM audit_event),
                (SELECT MAX(updated_at) FROM versand),
                (SELECT MAX(updated_at) FROM protokoll)
            '''
        )
        self.connection.commit()

        versand_incidents = list_versand_review_incidents(
            self.connection,
            settings,
        )

        self.assertEqual(len(versand_incidents), 1)
        self.assertEqual(
            versand_incidents[0].auftragsnummer,
            '00125083',
        )
        self.assertEqual(versand_incidents[0].reason, row[1])
        self.assertEqual(versand_incidents[0].linked_document_count, 1)
        self.assertEqual(
            versand_incidents[0].linked_documents[0].filename,
            pdf.name,
        )
        self.assertTrue(
            versand_incidents[0].automatic_retry_blocked
        )
        self.assertEqual(
            self._row(
                '''
                SELECT
                    (SELECT COUNT(*) FROM versand),
                    (SELECT COUNT(*) FROM protokoll),
                    (SELECT COUNT(*) FROM audit_event),
                    (SELECT MAX(updated_at) FROM versand),
                    (SELECT MAX(updated_at) FROM protokoll)
                '''
            ),
            read_only_snapshot,
        )
        self.connection.commit()
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand'),
            versand_before + 1,
        )
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*)
                FROM audit_event
                WHERE event_type = 'MOCK_MAIL_TIMEOUT_AMBIGUOUS'
                '''
            ),
            1,
        )
        self.connection.commit()
        eml_before_retry = len(list(outbox.glob('*.eml')))
        versand_before_retry = self._scalar('SELECT COUNT(*) FROM versand')
        audit_after_first = self._scalar('SELECT COUNT(*) FROM audit_event')
        self.connection.commit()

        second = prepare_ready_mock_versand(
            self.connection,
            settings,
            simulate_timeout=True,
        )

        self.assertEqual(second.ready_document_count, 0)
        self.assertEqual(second.already_processed_count, 1)
        self.assertEqual(second.messages, ())
        self.assertEqual(second.manual_review_incidents, ())
        self.assertEqual(len(list(outbox.glob('*.eml'))), eml_before_retry)
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand'),
            versand_before_retry,
        )
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM audit_event'),
            audit_after_first,
        )
        self.assertGreater(audit_after_first, audit_before)

    def test_crash_after_provider_becomes_manual_review_without_retry(
        self,
    ) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        pdf = root / '00125083_CRASH_APRES_MOCK.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic crash after provider'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(settings.mail, mock_outbox_path=outbox),
            database=self.settings,
        )
        scan_and_persist(self.connection, root, PATTERN)
        ready = scan_and_persist(self.connection, root, PATTERN)
        self.assertEqual(
            ready.report.items[0].status,
            ProtokollStatus.READY,
        )
        simulated_at = datetime(
            2026,
            8,
            14,
            16,
            0,
            tzinfo=timezone.utc,
        )
        crash_connection = connect_mysql(self.settings)
        try:
            with self.assertRaises(HomeDemoCrashAfterProvider) as raised:
                prepare_ready_mock_versand(
                    crash_connection,
                    settings,
                    run_at=simulated_at,
                    simulate_crash_after_provider=True,
                )
        finally:
            crash_connection.close()

        self.assertTrue(raised.exception.provider_called)
        self.assertTrue(raised.exception.message_path.is_file())
        self.assertEqual(len(list(outbox.glob('*.eml'))), 1)
        crashed = self._row(
            '''
            SELECT v.id, v.status, v.versuch_anzahl, v.accepted_at, p.status
            FROM versand AS v
            JOIN versand_protokoll AS vp ON vp.versand_id = v.id
            JOIN protokoll AS p ON p.id = vp.protokoll_id
            WHERE p.dateiname = '00125083_CRASH_APRES_MOCK.pdf'
            '''
        )
        self.assertEqual(crashed[1:], ('SENDING', 1, None, 'SENDING'))
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*) FROM audit_event
                WHERE event_type = 'MOCK_PROVIDER_CALL_STARTED'
                '''
            ),
            1,
        )
        self.connection.commit()

        recovery_connection = connect_mysql(self.settings)
        try:
            recovered = prepare_ready_mock_versand(
                recovery_connection,
                settings,
                run_at=simulated_at,
            )
        finally:
            recovery_connection.close()

        self.assertEqual(recovered.provider_call_count, 0)
        self.assertEqual(recovered.messages, ())
        self.assertEqual(len(recovered.manual_review_incidents), 1)
        incident = recovered.manual_review_incidents[0]
        self.assertEqual(incident.incident_type, 'CRASH_AFTER_PROVIDER')
        self.assertTrue(incident.provider_invoked)
        self.assertFalse(incident.message_accepted)
        self.assertTrue(incident.eml_created)
        self.assertTrue(incident.automatic_retry_blocked)
        self.assertEqual(
            self._row(
                '''
                SELECT v.id, v.status, v.versuch_anzahl, v.accepted_at, p.status
                FROM versand AS v
                JOIN versand_protokoll AS vp ON vp.versand_id = v.id
                JOIN protokoll AS p ON p.id = vp.protokoll_id
                WHERE p.dateiname = '00125083_CRASH_APRES_MOCK.pdf'
                '''
            ),
            (crashed[0], 'MANUAL_REVIEW', 1, None, 'MANUAL_REVIEW'),
        )
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*) FROM audit_event
                WHERE event_type =
                    'MOCK_PROVIDER_RESULT_AMBIGUOUS_AFTER_CRASH'
                '''
            ),
            1,
        )
        self.connection.commit()
        review_items = list_versand_review_incidents(
            self.connection,
            settings,
        )
        self.assertEqual(len(review_items), 1)
        self.assertEqual(review_items[0].versand_id, crashed[0])
        audit_count = self._scalar('SELECT COUNT(*) FROM audit_event')
        self.connection.commit()

        repeated = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )

        self.assertEqual(repeated.provider_call_count, 0)
        self.assertEqual(repeated.messages, ())
        self.assertEqual(repeated.manual_review_incidents, ())
        self.assertEqual(len(list(outbox.glob('*.eml'))), 1)
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 1)
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM audit_event'),
            audit_count,
        )

    def test_controlled_interruption_resumes_planned_versand_once(
        self,
    ) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        outbox = root / 'outbox'
        pdf = root / '00125083_REPRISE_AVANT_MOCK.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic pre-provider recovery'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )
        first_scan = scan_and_persist(self.connection, root, PATTERN)
        second_scan = scan_and_persist(self.connection, root, PATTERN)
        self.assertEqual(
            first_scan.report.items[0].status,
            ProtokollStatus.DISCOVERED,
        )
        self.assertEqual(
            second_scan.report.items[0].status,
            ProtokollStatus.READY,
        )
        simulated_at = datetime(
            2026,
            8,
            7,
            16,
            0,
            tzinfo=timezone.utc,
        )

        with self.assertRaises(HomeDemoControlledInterruption) as raised:
            prepare_ready_mock_versand(
                self.connection,
                settings,
                run_at=simulated_at,
                simulate_interruption_before_provider=True,
            )

        self.assertFalse(raised.exception.provider_called)
        self.assertEqual(len(list(outbox.glob('*.eml'))), 0)
        interrupted = self._row(
            '''
            SELECT
                v.id,
                v.status,
                v.versuch_anzahl,
                v.sending_started_at,
                v.accepted_at,
                p.status,
                p.sha256,
                v.period_start
            FROM versand AS v
            JOIN versand_protokoll AS vp ON vp.versand_id = v.id
            JOIN protokoll AS p ON p.id = vp.protokoll_id
            WHERE p.dateiname = '00125083_REPRISE_AVANT_MOCK.pdf'
            '''
        )
        self.assertEqual(interrupted[1], 'PLANNED')
        self.assertEqual(interrupted[2], 0)
        self.assertIsNone(interrupted[3])
        self.assertIsNone(interrupted[4])
        self.assertEqual(interrupted[5], 'SENDING')
        self.assertEqual(interrupted[6], calculate_sha256(pdf))
        self.assertEqual(interrupted[7], date(2026, 8, 3))
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*) FROM audit_event
                WHERE event_type = 'MOCK_PROVIDER_CALL_STARTED'
                '''
            ),
            0,
        )
        self.connection.commit()

        resumed = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )

        self.assertEqual(resumed.resumed_versand_count, 1)
        self.assertEqual(resumed.provider_call_count, 1)
        self.assertEqual(len(resumed.messages), 1)
        self.assertEqual(len(list(outbox.glob('*.eml'))), 1)
        message = BytesParser(policy=policy.default).parsebytes(
            resumed.messages[0].message_path.read_bytes()
        )
        self.assertEqual(
            [part.get_filename() for part in message.iter_attachments()],
            [pdf.name],
        )
        self.assertEqual(
            str(message['X-Pruefversand-Correlation-ID']),
            raised.exception.correlation_id,
        )
        self.assertEqual(
            self._row(
                '''
                SELECT v.id, v.status, v.versuch_anzahl, p.status
                FROM versand AS v
                JOIN versand_protokoll AS vp ON vp.versand_id = v.id
                JOIN protokoll AS p ON p.id = vp.protokoll_id
                WHERE p.dateiname = '00125083_REPRISE_AVANT_MOCK.pdf'
                '''
            ),
            (interrupted[0], 'ACCEPTED', 1, 'ACCEPTED'),
        )
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*) FROM audit_event
                WHERE event_type = 'MOCK_PROVIDER_CALL_STARTED'
                '''
            ),
            1,
        )
        self.connection.commit()
        versand_after_resume = self._scalar('SELECT COUNT(*) FROM versand')
        eml_after_resume = len(list(outbox.glob('*.eml')))
        self.connection.commit()

        repeated = prepare_ready_mock_versand(
            self.connection,
            settings,
            run_at=simulated_at,
        )

        self.assertFalse(repeated.weekly_due)
        self.assertEqual(repeated.provider_call_count, 0)
        self.assertEqual(repeated.messages, ())
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand'),
            versand_after_resume,
        )
        self.assertEqual(len(list(outbox.glob('*.eml'))), eml_after_resume)

    def test_manual_review_ignore_keeps_pdf_and_records_reason(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / 'F0661.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic manual ignore'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            database=self.settings,
        )
        scan_and_persist(self.connection, root, PATTERN)
        scan_and_persist(self.connection, root, PATTERN)
        pending = list_manual_reviews(self.connection, settings)

        result = ignore_manual_review(
            self.connection,
            settings,
            pending[0].protokoll_id,
            'Document synthetique hors perimetre',
        )

        self.assertEqual(result.action, 'IGNORE')
        self.assertTrue(pdf.is_file())
        self.assertEqual(list_manual_reviews(self.connection, settings), ())
        self.assertEqual(
            self._row(
                '''
                SELECT
                    p.status,
                    d.action,
                    d.reason,
                    d.assigned_auftrag_id,
                    d.decided_at IS NOT NULL
                FROM protokoll AS p
                JOIN manual_review_decision AS d
                    ON d.protokoll_id = p.id
                '''
            ),
            (
                'MANUAL_REVIEW',
                'IGNORE',
                'Document synthetique hors perimetre',
                None,
                1,
            ),
        )
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*)
                FROM audit_event
                WHERE event_type = 'MANUAL_REVIEW_IGNORE'
                '''
            ),
            1,
        )
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 0)

        third = scan_and_persist(self.connection, root, PATTERN)

        self.assertEqual(
            third.report.items[0].status,
            ProtokollStatus.MANUAL_REVIEW,
        )
        self.assertIn('Ignore manuellement', third.report.items[0].reason)
        self.assertEqual(list_manual_reviews(self.connection, settings), ())
        self.assertTrue(pdf.is_file())

    def test_manual_review_rejects_unstable_inactive_or_changed_pdf(
        self,
    ) -> None:
        import_baustellen_csv(self.connection, SAMPLE_CSV)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        pdf = root / '999999_F001.pdf'
        pdf.write_bytes(build_pdf_bytes('synthetic guarded review'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            database=self.settings,
        )
        scan_and_persist(self.connection, root, PATTERN)
        protokoll_id = int(self._scalar('SELECT id FROM protokoll'))
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE protokoll SET status = 'MANUAL_REVIEW' WHERE id = %s",
            (protokoll_id,),
        )
        self.connection.commit()
        cursor.close()

        with self.assertRaisesRegex(ManualReviewError, 'deux scans'):
            ignore_manual_review(
                self.connection,
                settings,
                protokoll_id,
                'Trop tot',
            )

        scan_and_persist(self.connection, root, PATTERN)
        cursor = self.connection.cursor()
        cursor.execute(
            '''
            UPDATE auftrag
            SET aktiv = FALSE
            WHERE auftragsnummer = '00125083'
            '''
        )
        self.connection.commit()
        cursor.close()

        with self.assertRaisesRegex(ManualReviewError, 'inactif'):
            assign_manual_review(
                self.connection,
                settings,
                protokoll_id,
                '00125083',
                'Auftrag desactive',
            )

        pdf.write_bytes(build_pdf_bytes('synthetic changed after scan'))
        with self.assertRaisesRegex(ManualReviewError, 'change'):
            ignore_manual_review(
                self.connection,
                settings,
                protokoll_id,
                'SHA obsolete',
            )

        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM manual_review_decision'),
            0,
        )
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 0)
        self.assertTrue(pdf.is_file())

    def test_manual_review_demo_creates_no_versand_or_eml(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source_pdf = (
            root
            / '00125083_F001_Pruefprotokoll_2026-07-25.pdf'
        )
        source_pdf.write_bytes(
            build_pdf_bytes('synthetic manual review demo')
        )
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        outbox = root / 'outbox'
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=outbox,
            ),
            database=self.settings,
        )

        result = run_manual_review_demo(self.connection, settings)

        self.assertEqual(
            {item.status for item in result.first_scan.items},
            {ProtokollStatus.DISCOVERED},
        )
        self.assertEqual(
            {item.status for item in result.second_scan.items},
            {ProtokollStatus.MANUAL_REVIEW},
        )
        second_by_name = {
            item.path.name: item
            for item in result.second_scan.items
        }
        self.assertEqual(
            second_by_name['999999_DEMO_Unbekannt.pdf'].auftragsnummer,
            '999999',
        )
        self.assertIsNone(
            second_by_name['F0661.pdf'].auftragsnummer
        )
        self.assertEqual(
            (result.versand_count_before, result.versand_count_after),
            (0, 0),
        )
        self.assertEqual(
            (result.eml_count_before, result.eml_count_after),
            (0, 0),
        )
        self.assertFalse(outbox.exists())
        self.assertEqual(
            self._scalar(
                '''
                SELECT COUNT(*)
                FROM protokoll
                WHERE status = 'MANUAL_REVIEW'
                  AND auftrag_id IS NULL
                '''
            ),
            2,
        )
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand'),
            0,
        )
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand_protokoll'),
            0,
        )

    def test_home_demo_writes_eml_with_synthetic_pdf(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source_pdf = (
            root
            / '00125083_F001_Pruefprotokoll_2026-07-25.pdf'
        )
        source_pdf.write_bytes(build_pdf_bytes('synthetic home demo'))
        settings = load_settings(
            PROJECT_ROOT / 'config/home.example.toml'
        )
        settings = replace(
            settings,
            app=replace(
                settings.app,
                environment='test',
                nas_path=root,
            ),
            mail=replace(
                settings.mail,
                mock_outbox_path=root / 'outbox',
            ),
            database=self.settings,
        )

        result = run_home_demo(self.connection, settings)

        self.assertEqual(
            result.first_scan.items[0].status,
            ProtokollStatus.DISCOVERED,
        )
        self.assertEqual(
            result.second_scan.items[0].status,
            ProtokollStatus.READY,
        )
        self.assertEqual(len(result.messages), 1)
        message_path = result.messages[0].message_path
        self.assertTrue(message_path.is_file())
        message = BytesParser(policy=policy.default).parsebytes(
            message_path.read_bytes()
        )
        self.assertTrue(str(message['Subject']).startswith('[TEST]'))
        addresses = [
            address
            for _, address in getaddresses(
                [str(message['To'] or ''), str(message['Cc'] or '')]
            )
            if address
        ]
        self.assertTrue(addresses)
        self.assertTrue(
            all(
                address.endswith('@example.invalid')
                for address in addresses
            )
        )
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_content_type(), 'application/pdf')
        self.assertEqual(
            attachments[0].get_filename(),
            result.pdf_path.name,
        )
        self.assertTrue(
            attachments[0].get_payload(decode=True).startswith(b'%PDF-')
        )

        eml_count = len(list((root / 'outbox').glob('*.eml')))
        replay = run_home_demo(self.connection, settings)

        self.assertEqual(replay.messages, ())
        self.assertEqual(replay.skipped_auftraege, ('00125083',))
        self.assertEqual(
            replay.reused_message_paths,
            (message_path,),
        )
        self.assertEqual(
            len(list((root / 'outbox').glob('*.eml'))),
            eml_count,
        )
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 1)
        self.assertEqual(
            self._scalar('SELECT COUNT(*) FROM versand_protokoll'),
            1,
        )

        cursor = self.connection.cursor()
        for table in (
            'versand_protokoll',
            'versand',
            'manual_review_decision',
            'scan_observation',
            'protokoll',
            'audit_event',
            'csv_import',
            'empfaenger',
            'auftrag',
            'baustelle',
        ):
            cursor.execute(f'DELETE FROM {table}')
        self.connection.commit()
        cursor.close()

        reconciled = run_home_demo(self.connection, settings)

        self.assertEqual(reconciled.messages, ())
        self.assertEqual(reconciled.reused_message_paths, (message_path,))
        self.assertEqual(reconciled.skipped_auftraege, ('00125083',))
        self.assertEqual(
            len(list((root / 'outbox').glob('*.eml'))),
            eml_count,
        )
        self.assertEqual(self._scalar('SELECT COUNT(*) FROM versand'), 1)


if __name__ == '__main__':
    unittest.main()
