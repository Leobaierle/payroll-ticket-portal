from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import http.client
import unittest
import csv
import io
import types
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class Projeto1TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["APP_DB_PATH"] = str(Path(self.temp_dir.name) / "test.db")
        os.environ["APP_OUTBOX_PATH"] = str(Path(self.temp_dir.name) / "outbox")
        os.environ["APP_ATTACHMENTS_PATH"] = str(Path(self.temp_dir.name) / "attachments")
        os.environ["APP_ENV"] = "test"
        os.environ["DB_BACKEND"] = "sqlite"
        os.environ["APP_SECRETS_ENV_FILE"] = str(Path(self.temp_dir.name) / "missing-secrets.env")
        os.environ["APP_BASE_URL"] = "http://localhost:8000"
        os.environ["MAIL_FROM"] = "no-reply@test.local"
        os.environ["MAIL_REPLY_TO"] = ""
        os.environ["SMTP_HOST"] = ""
        os.environ["SMTP_PORT"] = "587"
        os.environ["SMTP_USERNAME"] = ""
        os.environ["SMTP_PASSWORD"] = ""
        os.environ["SMTP_USE_TLS"] = "1"
        os.environ["SMTP_USE_SSL"] = "0"
        os.environ.pop("DATABASE_URL", None)

        import importlib

        self.app_config = importlib.import_module("app_config")
        importlib.reload(self.app_config)
        self.app_db = importlib.import_module("app_db")
        importlib.reload(self.app_db)
        self.app_services = importlib.import_module("app_services")
        importlib.reload(self.app_services)
        self.server_module = importlib.import_module("server")
        importlib.reload(self.server_module)
        self.app_db.ensure_schema()

    def tearDown(self) -> None:
        os.environ.pop("APP_SECRETS_ENV_FILE", None)
        self.temp_dir.cleanup()

    def test_app_config_carrega_arquivo_de_segredos_antes_do_env_local(self) -> None:
        secrets_path = Path(self.temp_dir.name) / "secrets.env"
        secrets_path.write_text("DATABASE_URL=postgresql://segredo\nSMTP_PASSWORD=topsecret\n", encoding="utf-8")
        os.environ["APP_SECRETS_ENV_FILE"] = str(secrets_path)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("SMTP_PASSWORD", None)

        import importlib

        importlib.reload(self.app_config)

        self.assertEqual(self.app_config.DATABASE_URL, "postgresql://segredo")
        self.assertEqual(self.app_config.SMTP_PASSWORD, "topsecret")

    def start_http_server(self):
        httpd = self.server_module.ThreadingHTTPServer(("127.0.0.1", 0), self.server_module.AppHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread

    def request_http(self, method: str, path: str, body: str = "", headers: dict[str, str] | None = None):
        httpd, thread = self.start_http_server()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read().decode("utf-8", errors="replace")
            header_map = {key: value for key, value in response.getheaders()}
            set_cookies = [value for key, value in response.getheaders() if key.lower() == "set-cookie"]
            connection.close()
            return response.status, payload, header_map, set_cookies
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_abertura_de_chamado(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Erro em rotina mensal",
                "description": "A rotina mensal falhou ao consolidar os dados do fechamento.",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)
        token = self.app_services.get_public_ticket_token_by_id(ticket_id)
        self.assertTrue(token)

    def test_abertura_de_chamado_rescisao_exige_tipo_e_persiste_na_descricao(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Rescisão",
                "title": "Desligamento de colaborador",
                "description": "Favor prosseguir com o desligamento ainda nesta semana.",
            }
        )
        self.assertIn("rescission_type", errors)
        self.assertIsNone(ticket_id)

        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Rescisão",
                "rescission_subcategory": "Cálculo de rescisão",
                "rescission_type": "Término de contrato antecipado",
                "rescission_date": "2026-10-30",
                "title": "Desligamento de colaborador",
                "description": "Favor prosseguir com o desligamento ainda nesta semana.",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertIn("Categoria: Cálculo de rescisão", ticket["description"])
        self.assertIn("Tipo de rescisão: Término de contrato antecipado", ticket["description"])

    def _rescisao_form(self, **extra) -> dict:
        form = {
            "requester_name": "Maria Teste",
            "category_id": "1",
            "company_name": "Alfa Matriz",
            "request_category": "Rescisão",
            "rescission_type": "Pedido de demissão",
            "rescission_date": "2026-10-30",
            "description": "Favor prosseguir com o desligamento nesta semana.",
        }
        form.update(extra)
        return form

    def test_rescisao_sem_titulo_com_campos_novos_na_descricao(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            self._rescisao_form(
                rescission_registration="1234",
                rescission_employee_name="Maria da Silva",
                rescission_event_0_name="Horas extras",
                rescission_event_0_value="150,00",
                rescission_event_1_name="Comissão",
            )
        )
        self.assertFalse(errors, errors)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertEqual(ticket["title"], "Rescisão - Maria da Silva")
        self.assertEqual(
            ticket["description"],
            "Tipo de rescisão: Pedido de demissão\n"
            "Matrícula: 1234\n"
            "Nome do funcionário: Maria da Silva\n"
            "Data da rescisão: 30/10/2026\n\n"
            "Favor prosseguir com o desligamento nesta semana.\n\n"
            "Eventos:\nHoras extras: 150,00\nComissão",
        )

    def test_rescisao_matricula_nome_e_eventos_sao_opcionais(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(self._rescisao_form())
        self.assertFalse(errors, errors)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertEqual(ticket["title"], "Rescisão - Pedido de demissão")
        self.assertNotIn("Matrícula", ticket["description"])
        self.assertNotIn("Nome do funcionário", ticket["description"])
        self.assertNotIn("Eventos", ticket["description"])

    def test_rescisao_exige_data_valida(self) -> None:
        for valor in ("", "30/10/2026", "2026-02-31", "0026-10-30"):
            _, errors, ticket_id = self.app_services.create_ticket(self._rescisao_form(rescission_date=valor))
            self.assertIn("rescission_date", errors, valor)
            self.assertIsNone(ticket_id)

    def test_rescisao_com_erro_devolve_campos_como_digitados(self) -> None:
        normalized, errors, _ = self.app_services.create_ticket(
            self._rescisao_form(rescission_date="", rescission_employee_name="Maria")
        )
        self.assertIn("rescission_date", errors)
        self.assertEqual(normalized["description"], "Favor prosseguir com o desligamento nesta semana.")
        self.assertEqual(normalized["rescission_employee_name"], "Maria")

    def test_outras_categorias_continuam_exigindo_titulo(self) -> None:
        _, errors, _ = self.app_services.create_ticket(
            self._rescisao_form(request_category="Folha mensal", title="")
        )
        self.assertIn("title", errors)
        self.assertNotIn("rescission_date", errors)

    def test_formulario_de_rescisao_tem_campos_na_ordem_pedida(self) -> None:
        pagina = self.server_module.public_form_page(
            self.app_services.list_categories(),
            "csrf",
            form_data={"category_id": "1", "request_category": "Rescisão"},
        )
        self.assertNotIn('name="title"', pagina)
        ordem = [
            'name="rescission_type"',
            'name="rescission_registration"',
            'name="rescission_employee_name"',
            'name="rescission_date"',
            'name="description"',
            'data-add-rescission-event',
        ]
        posicoes = [pagina.index(campo) for campo in ordem]
        self.assertEqual(posicoes, sorted(posicoes))

        outra = self.server_module.public_form_page(
            self.app_services.list_categories(),
            "csrf",
            form_data={"category_id": "1", "request_category": "Folha mensal"},
        )
        self.assertIn('name="title"', outra)
        self.assertNotIn('name="rescission_date"', outra)

    def _ferias_form(self, **extra) -> dict:
        form = {
            "requester_name": "Maria Teste",
            "category_id": "1",
            "company_name": "Alfa Matriz",
            "request_category": "Férias",
            "vacation_employee_name": "Maria da Silva",
            "vacation_start_date": "2026-11-03",
            "vacation_days": "30",
            "vacation_has_bonus": "Não",
        }
        form.update(extra)
        return form

    def test_ferias_com_matricula_aparece_na_descricao_e_nos_dados(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(self._ferias_form(vacation_registration="1234"))
        self.assertFalse(errors, errors)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertTrue(ticket["description"].startswith("Matrícula: 1234\nNome do funcionário: Maria da Silva"))
        detalhes = self.server_module.render_admission_ticket_details(ticket)
        self.assertIn("Matrícula", detalhes)
        self.assertIn("1234", detalhes)

    def test_ferias_matricula_e_opcional_e_tem_limite(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(self._ferias_form())
        self.assertFalse(errors, errors)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertNotIn("Matrícula", ticket["description"])
        _, errors, _ = self.app_services.create_ticket(self._ferias_form(vacation_registration="1" * 31))
        self.assertIn("vacation_registration", errors)

    def test_formulario_de_ferias_mostra_matricula_antes_do_nome(self) -> None:
        pagina = self.server_module.public_form_page(
            self.app_services.list_categories(),
            "csrf",
            form_data={"category_id": "1", "request_category": "Férias"},
        )
        self.assertLess(pagina.index('name="vacation_registration"'), pagina.index('name="vacation_employee_name"'))

    def test_rescisao_eventos_ignora_linhas_vazias_e_buracos_na_numeracao(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            self._rescisao_form(
                rescission_event_0_name="Primeiro",
                rescission_event_0_value="1,00",
                rescission_event_2_name="  ",
                rescission_event_2_value="",
                rescission_event_5_name="Segundo",
                rescission_event_5_value="2,00",
            )
        )
        self.assertFalse(errors, errors)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertTrue(ticket["description"].endswith("Eventos:\nPrimeiro: 1,00\nSegundo: 2,00"))

    def test_rescisao_evento_com_valor_sem_nome_e_recusado_e_mantem_linhas(self) -> None:
        normalized, errors, ticket_id = self.app_services.create_ticket(
            self._rescisao_form(
                rescission_event_0_name="Primeiro",
                rescission_event_3_value="9,99",
            )
        )
        self.assertIsNone(ticket_id)
        self.assertEqual(errors.get("rescission_event_1_name"), "Informe o evento.")
        self.assertEqual(normalized["rescission_event_1_value"], "9,99")
        pagina = self.server_module.public_form_page(
            self.app_services.list_categories(), "csrf", form_data=normalized, errors=errors
        )
        self.assertIn('value="Primeiro"', pagina)
        self.assertIn('value="9,99"', pagina)
        self.assertIn("Informe o evento.", pagina)

    def test_rescisao_limita_quantidade_e_tamanho_dos_eventos(self) -> None:
        muitos = {f"rescission_event_{i}_name": f"Evento {i}" for i in range(31)}
        _, errors, _ = self.app_services.create_ticket(self._rescisao_form(**muitos))
        self.assertIn("rescission_events", errors)
        _, errors, _ = self.app_services.create_ticket(
            self._rescisao_form(rescission_event_0_name="x" * 121, rescission_event_0_value="1" * 41)
        )
        self.assertIn("rescission_event_0_name", errors)
        self.assertIn("rescission_event_0_value", errors)

    def test_exclusao_de_ticket_remove_arquivo_de_anexo(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Erro em rotina mensal",
                "description": "A rotina mensal falhou ao consolidar os dados do fechamento.",
            },
            attachment={
                "filename": "holerite.pdf",
                "content": b"arquivo teste",
                "content_type": "application/pdf",
                "size_bytes": 13,
            },
        )
        self.assertFalse(errors)
        attachments = self.app_services.list_ticket_attachments(ticket_id)
        self.assertEqual(len(attachments), 1)
        _, attachment_path = self.app_services.get_ticket_attachment(attachments[0]["id"])
        self.assertTrue(attachment_path.exists())

        success, _ = self.app_services.delete_ticket(ticket_id)
        self.assertTrue(success)
        self.assertFalse(attachment_path.exists())

    def test_cleanup_de_anexos_orfaos_remove_arquivos_sem_referencia(self) -> None:
        orphan = Path(os.environ["APP_ATTACHMENTS_PATH"]) / "orphan.txt"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("arquivo perdido", encoding="utf-8")

        removed = self.app_services.cleanup_orphan_attachment_files()

        self.assertEqual(removed, [orphan])
        self.assertFalse(orphan.exists())

    def test_falha_durante_evento_limpa_arquivo_de_anexo_criado(self) -> None:
        original_record_ticket_event = self.app_services.record_ticket_event
        calls = {"count": 0}

        def failing_record_ticket_event(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] > 1:
                raise RuntimeError("falha forçada")
            return original_record_ticket_event(*args, **kwargs)

        with mock.patch.object(self.app_services, "record_ticket_event", side_effect=failing_record_ticket_event):
            with self.assertRaises(RuntimeError):
                self.app_services.create_ticket(
                    {
                        "requester_name": "Maria Teste",
                        "category_id": "1",
                        "company_name": "Alfa Matriz",
                        "request_category": "Folha mensal",
                        "title": "Erro em rotina mensal",
                        "description": "A rotina mensal falhou ao consolidar os dados do fechamento.",
                    },
                    attachment={
                        "filename": "holerite.pdf",
                        "content": b"arquivo teste",
                        "content_type": "application/pdf",
                        "size_bytes": 13,
                    },
                )

        attachment_files = [path for path in Path(os.environ["APP_ATTACHMENTS_PATH"]).iterdir() if path.name != ".gitkeep"]
        self.assertFalse(attachment_files)

    def test_abertura_de_chamado_admissao_gera_titulo_e_descricao_a_partir_dos_campos(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Admissão",
                "employee_name": "Maria da Silva",
                "employee_cpf": "12345678901",
                "employee_birth_date": "1990-01-10",
                "employee_role": "Auxiliar administrativo",
                "employee_cbo": "4110-05",
                "employee_admission_date": "2026-05-20",
                "employee_salary": "2500,00",
                "employee_work_schedule": "Seg a sex 08:00 as 17:48",
                "employee_experience_contract": "30/60",
                "employee_insalubrity": "Sim",
                "employee_insalubrity_percentage": "20%",
                "employee_hazard_pay": "Não",
                "employee_transport_voucher": "Sim",
                "employee_meal_voucher": "Sim",
                "employee_ethnicity": "Branco",
                "employee_gender": "Feminino",
                "employee_education": "Ensino médio Completo",
                "employee_zip_code": "90000000",
                "employee_address": "Rua Exemplo",
                "employee_address_number": "123",
                "employee_phone": "(51) 99999-0001",
                "employee_mother_name": "Joana da Silva",
                "employee_birth_city": "Porto Alegre",
                "employee_birth_state": "RS",
                "employee_rg_issue_date": "2015-01-10",
                "employee_work_card_type": "Digital",
                "employee_department": "Administrativo",
                "employee_cost_center": "ADM-01",
                "employee_advance_enabled": "Não",
                "employee_city": "Porto Alegre",
                "employee_neighborhood": "Centro",
                "employee_state": "RS",
                "employee_marital_status": "Solteiro",
                "employee_has_alimony": "Não",
                "employee_bank_name": "Banco Teste",
                "employee_bank_account_type": "Corrente",
                "employee_bank_account_number": "12345-6",
                "employee_bank_agency": "0001",
                "employee_pix_key": "12345678901",
                "employee_pix_key_type": "CPF",
                "dependent_0_name": "Dependente Teste",
                "dependent_0_cpf": "98765432100",
                "dependent_0_birth_date": "2018-02-03",
                "dependent_0_relationship": "Filho(a) ou enteado(a)",
                "dependent_0_ir_dependent": "Sim",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertEqual(ticket["title"], "Admissão - Maria da Silva")
        self.assertIn("Nome do funcionário: Maria da Silva", ticket["description"])
        self.assertIn("CPF: 12345678901", ticket["description"])
        self.assertIn("Carteira de trabalho: Digital", ticket["description"])
        self.assertIn("Dependente 1:", ticket["description"])
        self.assertIn("Nome: Dependente Teste", ticket["description"])
        payload = json.loads(ticket["admission_payload"])
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["employee"]["employee_name"], "Maria da Silva")
        self.assertEqual(payload["employee"]["employee_cpf"], "12345678901")
        self.assertEqual(payload["dependents"][0]["name"], "Dependente Teste")
        self.assertIn("maria da silva", ticket["admission_search_text"])
        self.assertIn("98765432100", ticket["admission_search_text"])

    def test_abertura_de_chamado_admissao_dependente_exige_campos_obrigatorios(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Admissão",
                "employee_name": "Maria da Silva",
                "employee_cpf": "12345678901",
                "employee_birth_date": "1990-01-10",
                "employee_role": "Auxiliar administrativo",
                "employee_cbo": "4110-05",
                "employee_admission_date": "2026-05-20",
                "employee_salary": "2500,00",
                "employee_work_schedule": "Seg a sex 08:00 as 17:48",
                "employee_experience_contract": "30/60",
                "employee_insalubrity": "Não",
                "employee_hazard_pay": "Não",
                "employee_transport_voucher": "Sim",
                "employee_meal_voucher": "Sim",
                "employee_ethnicity": "Branco",
                "employee_gender": "Feminino",
                "employee_education": "Ensino médio Completo",
                "employee_zip_code": "90000000",
                "employee_address": "Rua Exemplo",
                "employee_address_number": "123",
                "employee_phone": "(51) 99999-0001",
                "employee_mother_name": "Joana da Silva",
                "employee_birth_city": "Porto Alegre",
                "employee_birth_state": "RS",
                "employee_rg_issue_date": "2015-01-10",
                "employee_work_card_type": "Digital",
                "dependent_0_name": "Dependente Teste",
            }
        )
        self.assertIsNone(ticket_id)
        self.assertIn("dependent_0_cpf", errors)
        self.assertIn("dependent_0_birth_date", errors)
        self.assertIn("dependent_0_relationship", errors)
        self.assertIn("dependent_0_ir_dependent", errors)

    def test_abertura_de_chamado_admissao_limpa_campos_condicionais_ocultos(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Admissão",
                "employee_name": "Carlos Dias",
                "employee_cpf": "123.456.789-01",
                "employee_birth_date": "1990-01-10",
                "employee_role": "Auxiliar administrativo",
                "employee_cbo": "4110-05",
                "employee_admission_date": "2026-05-20",
                "employee_salary": "2500,00",
                "employee_work_schedule": "Seg a sex 08:00 as 17:48",
                "employee_experience_contract": "30/60",
                "employee_insalubrity": "Não",
                "employee_insalubrity_percentage": "40%",
                "employee_hazard_pay": "Não",
                "employee_transport_voucher": "Sim",
                "employee_meal_voucher": "Sim",
                "employee_ethnicity": "Branco",
                "employee_gender": "Masculino",
                "employee_education": "Ensino médio Completo",
                "employee_zip_code": "90000-000",
                "employee_address": "Rua Exemplo",
                "employee_address_number": "123",
                "employee_phone": "(51) 99999-0002",
                "employee_mother_name": "Joana da Silva",
                "employee_birth_city": "Porto Alegre",
                "employee_birth_state": "RS",
                "employee_rg_issue_date": "2015-01-10",
                "employee_work_card_type": "Digital",
                "employee_work_card_number": "9999",
                "employee_work_card_series": "12",
                "employee_work_card_state": "rs",
                "employee_department": "Administrativo",
                "employee_cost_center": "ADM-01",
                "employee_advance_enabled": "Não",
                "employee_city": "Porto Alegre",
                "employee_neighborhood": "Centro",
                "employee_state": "RS",
                "employee_marital_status": "Solteiro",
                "employee_has_alimony": "Não",
                "employee_bank_name": "Banco Teste",
                "employee_bank_account_type": "Corrente",
                "employee_bank_account_number": "12345-6",
                "employee_bank_agency": "0001",
                "employee_pix_key": "12345678901",
                "employee_pix_key_type": "CPF",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        payload = json.loads(ticket["admission_payload"])
        self.assertEqual(payload["employee"]["employee_cpf"], "12345678901")
        self.assertEqual(payload["employee"]["employee_zip_code"], "90000000")
        self.assertEqual(payload["employee"]["employee_insalubrity_percentage"], "")
        self.assertEqual(payload["employee"]["employee_work_card_number"], "")
        self.assertEqual(payload["employee"]["employee_work_card_series"], "")
        self.assertEqual(payload["employee"]["employee_work_card_state"], "")

    def test_abertura_de_chamado_admissao_normaliza_campos_operacionais(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Admissão",
                "employee_name": "Maria da Silva",
                "employee_cpf": "123.456.789-01",
                "employee_birth_date": "1998-02-01",
                "employee_role": "Auxiliar",
                "employee_cbo": "1234",
                "employee_admission_date": "2026-05-20",
                "employee_salary": "R$ 2.500,00",
                "employee_work_schedule": "08h às 18h",
                "employee_experience_contract": "30/60",
                "employee_insalubrity": "Não",
                "employee_hazard_pay": "Não",
                "employee_transport_voucher": "Sim",
                "employee_meal_voucher": "Sim",
                "employee_ethnicity": "Pardo",
                "employee_gender": "Feminino",
                "employee_education": "Ensino médio Completo",
                "employee_zip_code": "90.010-123",
                "employee_address": "Rua A",
                "employee_address_number": "100",
                "employee_city": "Porto Alegre",
                "employee_neighborhood": "Centro",
                "employee_state": "rs",
                "employee_phone": "(51) 99876-5432",
                "employee_mother_name": "Ana da Silva",
                "employee_birth_city": "Porto Alegre",
                "employee_birth_state": "sc",
                "employee_pis": "123.45678.90-1",
                "employee_rg": "10.203.405-6",
                "employee_rg_issuer_state": "sc",
                "employee_rg_issue_date": "2018-04-10",
                "employee_work_card_type": "Digital",
                "employee_department": "Administrativo",
                "employee_cost_center": "ADM-01",
                "employee_advance_enabled": "Não",
                "employee_marital_status": "Solteiro",
                "employee_has_alimony": "Não",
                "employee_bank_name": "Banco Teste",
                "employee_bank_account_type": "Corrente",
                "employee_bank_account_number": "12345-6",
                "employee_bank_agency": "0001",
                "employee_pix_key": "12345678901",
                "employee_pix_key_type": "CPF",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        payload = json.loads(ticket["admission_payload"])
        self.assertEqual(payload["employee"]["employee_salary"], "2500,00")
        self.assertEqual(payload["employee"]["employee_zip_code"], "90010123")
        self.assertEqual(payload["employee"]["employee_phone"], "51998765432")
        self.assertEqual(payload["employee"]["employee_pis"], "12345678901")
        self.assertEqual(payload["employee"]["employee_rg"], "102034056")
        self.assertEqual(payload["employee"]["employee_state"], "RS")
        self.assertEqual(payload["employee"]["employee_rg_issuer_state"], "SC")

    def test_abertura_de_chamado_admissao_rejeita_formatos_operacionais_invalidos(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Admissão",
                "employee_name": "Maria da Silva",
                "employee_cpf": "123.456.789-01",
                "employee_birth_date": "1998-02-01",
                "employee_role": "Auxiliar",
                "employee_cbo": "1234",
                "employee_admission_date": "2026-05-20",
                "employee_salary": "abc",
                "employee_work_schedule": "08h às 18h",
                "employee_experience_contract": "30/60",
                "employee_insalubrity": "Não",
                "employee_hazard_pay": "Não",
                "employee_transport_voucher": "Sim",
                "employee_meal_voucher": "Sim",
                "employee_ethnicity": "Pardo",
                "employee_gender": "Feminino",
                "employee_education": "Ensino médio Completo",
                "employee_zip_code": "90010",
                "employee_address": "Rua A",
                "employee_address_number": "100",
                "employee_state": "rio",
                "employee_phone": "12345",
                "employee_mother_name": "Ana da Silva",
                "employee_birth_city": "Porto Alegre",
                "employee_birth_state": "SC",
                "employee_pis": "12345",
                "employee_rg_issuer_state": "r",
                "employee_rg_issue_date": "2018-04-10",
                "employee_work_card_type": "Digital",
            }
        )
        self.assertIsNone(ticket_id)
        self.assertEqual(errors["employee_zip_code"], "Informe um CEP com 8 dígitos.")
        self.assertEqual(errors["employee_phone"], "Informe um telefone com DDD válido.")
        self.assertEqual(errors["employee_pis"], "Informe um PIS com 11 dígitos.")
        self.assertEqual(errors["employee_rg_issuer_state"], "Informe uf do rg com 2 letras.")

    def test_formulario_publico_renderiza_bloco_de_dependentes_e_mascaras_de_admissao(self) -> None:
        html = self.server_module.public_form_page(
            self.app_services.list_categories(),
            "csrf-teste",
            user={"role": "dp", "name": "Teste DP"},
            form_data={"category_id": "1", "request_category": "Admissão"},
        )

        self.assertIn('data-admission-container', html)
        self.assertIn('data-dependent-section', html)
        self.assertIn('data-dependent-list', html)
        self.assertIn('data-add-dependent', html)
        self.assertIn('data-dependent-template', html)
        self.assertIn('name="dependent_0_name"', html)
        self.assertIn('name="dependent_0_cpf"', html)
        self.assertIn('data-mask="cpf"', html)
        self.assertIn('data-mask="cep"', html)
        self.assertIn('data-mask="phone"', html)
        self.assertIn('data-mask="pis"', html)
        self.assertIn('data-mask="currency"', html)
        self.assertIn('data-mask="rg"', html)

    def test_formulario_publico_embute_regras_dinamicas_de_dependentes_e_condicionais(self) -> None:
        html = self.server_module.public_form_page(
            self.app_services.list_categories(),
            "csrf-teste",
            user={"role": "dp", "name": "Teste DP"},
            form_data={"category_id": "1", "request_category": "Admissão"},
        )

        self.assertIn("replaceAll('dependent_0_'", html)
        self.assertIn("card.remove();", html)
        self.assertIn("updateDependentHeadings();", html)
        self.assertIn("wrapper.classList.toggle('is-hidden'", html)
        self.assertIn("control.required = shouldShowStructured", html)
        self.assertIn('data-required-when-field="employee_insalubrity"', html)
        self.assertIn('data-show-when-field="employee_work_card_type"', html)
        self.assertNotIn('data-required-when-field="employee_work_card_type"', html)

    def test_abertura_de_chamado_ferias_gera_titulo_e_descricao_a_partir_dos_campos(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Férias",
                "vacation_subcategory": "Recibo de férias",
                "vacation_employee_name": "Maria da Silva",
                "vacation_start_date": "2026-07-08",
                "vacation_days": "20",
                "vacation_has_bonus": "Sim",
                "vacation_bonus_days": "10",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertEqual(ticket["title"], "Recibo de férias - Maria da Silva")
        self.assertIn("Nome do funcionário: Maria da Silva", ticket["description"])
        self.assertIn("Data de início: 2026-07-08", ticket["description"])
        self.assertIn("Dias de férias: 20", ticket["description"])
        self.assertIn("Abono de férias: Sim", ticket["description"])
        self.assertIn("Dias de abono: 10", ticket["description"])
        payload = json.loads(ticket["admission_payload"])
        self.assertEqual(payload["vacation"]["vacation_employee_name"], "Maria da Silva")
        self.assertEqual(payload["vacation"]["vacation_bonus_days"], "10")
        self.assertIn("maria da silva", ticket["admission_search_text"])

    def test_abertura_de_chamado_ferias_exige_dias_de_abono_quando_habilitado(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Férias",
                "vacation_employee_name": "Maria da Silva",
                "vacation_start_date": "2026-07-08",
                "vacation_days": "20",
                "vacation_has_bonus": "Sim",
            }
        )
        self.assertIsNone(ticket_id)
        self.assertIn("vacation_bonus_days", errors)

    def test_abertura_de_chamado_ferias_rejeita_inicio_entre_quinta_e_domingo(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Férias",
                "vacation_employee_name": "Maria da Silva",
                "vacation_start_date": "2026-07-09",
                "vacation_days": "20",
                "vacation_has_bonus": "Não",
            }
        )
        self.assertIsNone(ticket_id)
        self.assertEqual(errors.get("vacation_start_date"), "O início das férias deve ser entre segunda e quarta-feira.")

    def test_categorias_da_solicitacao_incluem_processos_trabalhistas_e_outros(self) -> None:
        self.assertIn("Processos trabalhistas", self.app_services.REQUEST_CATEGORY_OPTIONS)
        self.assertIn("Outros", self.app_services.REQUEST_CATEGORY_OPTIONS)
        self.assertEqual(
            self.app_services.company_options_for_category("Processos trabalhistas"),
            (),
        )
        self.assertEqual(
            self.app_services.company_options_for_category("Outros"),
            (),
        )
        categories = self.app_services.list_categories()
        names = [row["name"] for row in categories]
        self.assertEqual(names, ["Alfa", "Beta", "Gama"])

    def test_abertura_de_chamado_aceita_processos_trabalhistas_e_outros_na_categoria(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Marcos Viana",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Processos trabalhistas",
                "title": "Analise juridica",
                "description": "Solicitação criada para validar a nova categoria da abertura.",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)

        _, errors, other_ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Marcos Viana",
                "category_id": "2",
                "company_name": "Beta Filial",
                "request_category": "Outros",
                "title": "Ajuste geral",
                "description": "Solicitação criada para validar a nova categoria de abertura.",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(other_ticket_id)

    def test_setup_login_e_sessao(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "dp")
        token = self.app_services.create_session(user["id"])
        session_user = self.app_services.get_session_user(token)
        self.assertEqual(session_user["email"], "ti@example.com")

    def test_importar_server_aplica_migracoes_pendentes_em_banco_legado(self) -> None:
        import importlib
        import sqlite3

        legacy_db = Path(os.environ["APP_DB_PATH"])
        if legacy_db.exists():
            legacy_db.unlink()

        conn = sqlite3.connect(legacy_db)
        conn.executescript((PROJECT_ROOT / "db/migrations/001_initial.sql").read_text(encoding="utf-8"))
        conn.executescript((PROJECT_ROOT / "db/migrations/002_upgrade.sql").read_text(encoding="utf-8"))
        conn.executescript((PROJECT_ROOT / "db/migrations/003_cleanup_legacy_schema.sql").read_text(encoding="utf-8"))
        conn.executescript((PROJECT_ROOT / "db/migrations/004_login_rate_limit_store.sql").read_text(encoding="utf-8"))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version in (
            "001_initial.sql",
            "002_upgrade.sql",
            "003_cleanup_legacy_schema.sql",
            "004_login_rate_limit_store.sql",
            "011_request_types_additional_categories.sql",
        ):
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()
        conn.close()

        importlib.reload(self.app_db)
        importlib.reload(self.app_services)
        importlib.reload(self.server_module)

        with self.app_db.get_db() as conn:
            user_invites_exists = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'user_invites'"
            ).fetchone()[0]
            ticket_attachments_exists = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'ticket_attachments'"
            ).fetchone()[0]
            ticket_columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
            request_types = [row["name"] for row in conn.execute("SELECT name FROM request_types ORDER BY id")]
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

        self.assertEqual(user_invites_exists, 1)
        self.assertEqual(ticket_attachments_exists, 1)
        self.assertIn("company_name", ticket_columns)
        self.assertIn("request_category", ticket_columns)
        self.assertIn("admission_payload", ticket_columns)
        self.assertIn("admission_search_text", ticket_columns)
        self.assertIn("008_ticket_company_name.sql", applied)
        self.assertIn("009_user_roles_and_invites.sql", applied)
        self.assertIn("010_ticket_request_category_and_attachments.sql", applied)
        self.assertIn("011_request_types_additional_categories.sql", applied)
        self.assertIn("012_request_types_remove_additional_categories.sql", applied)
        self.assertIn("013_ticket_admission_payload.sql", applied)
        self.assertEqual(request_types, ["Alfa", "Beta", "Gama"])

    def test_convite_ativa_usuario_beta_por_link(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Admin DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        admin = self.app_services.authenticate_user("dp@example.com", "Senha123")
        _, invite_errors, invite_id = self.app_services.create_user_invite(
            {"name": "Usuario Beta", "email": "beta@example.com", "role": "beta"},
            admin["id"],
        )
        self.assertFalse(invite_errors)
        self.assertIsNotNone(invite_id)
        delivery_mode = self.app_services.send_user_invite_email(invite_id)
        self.assertEqual(delivery_mode, "outbox")
        outbox_dir = Path(os.environ["APP_OUTBOX_PATH"])
        files = list(outbox_dir.glob("*.eml"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("/convites/", content)

        invite = self.app_services.get_user_invite(
            next(line.rsplit("/", 1)[-1] for line in content.splitlines() if "/convites/" in line)
        )
        self.assertIsNotNone(invite)
        success, message = self.app_services.accept_user_invite(
            invite["invite_token"],
            {"password": "Senha123", "confirm_password": "Senha123"},
        )
        self.assertTrue(success, message)
        invited_user = self.app_services.authenticate_user("beta@example.com", "Senha123")
        self.assertIsNotNone(invited_user)
        self.assertEqual(invited_user["role"], "beta")
        self.assertEqual(invited_user["name"], "Usuario Beta")

    def test_convite_ativa_usuario_ambos_por_link(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Admin DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        admin = self.app_services.authenticate_user("dp@example.com", "Senha123")
        _, invite_errors, invite_id = self.app_services.create_user_invite(
            {"name": "Usuario Ambos", "email": "ambos@example.com", "role": "ambos"},
            admin["id"],
        )
        self.assertFalse(invite_errors)
        self.assertIsNotNone(invite_id)
        delivery_mode = self.app_services.send_user_invite_email(invite_id)
        self.assertEqual(delivery_mode, "outbox")
        outbox_dir = Path(os.environ["APP_OUTBOX_PATH"])
        files = list(outbox_dir.glob("*.eml"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        invite = self.app_services.get_user_invite(
            next(line.rsplit("/", 1)[-1] for line in content.splitlines() if "/convites/" in line)
        )
        self.assertEqual(invite["role"], "ambos")
        success, message = self.app_services.accept_user_invite(
            invite["invite_token"],
            {"password": "Senha123", "confirm_password": "Senha123"},
        )
        self.assertTrue(success, message)
        invited_user = self.app_services.authenticate_user("ambos@example.com", "Senha123")
        self.assertIsNotNone(invited_user)
        self.assertEqual(invited_user["role"], "ambos")

    def test_atualizacao_de_chamado(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Silva",
                "category_id": "2",
                "company_name": "Beta",
                "request_category": "Admissão",
                "title": "Acesso ao sistema",
                "description": "Não consigo acessar o sistema interno da empresa.",
            }
        )
        self.assertFalse(errors)
        success, message = self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Em atendimento",
                "internal_note": "Atendimento iniciado",
                "comment": "Contato inicial realizado com a usuaria.",
                "assign_to_me": "1",
            },
            user["id"],
        )
        self.assertTrue(success, message)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertEqual(ticket["status"], "Em atendimento")

    def test_criacao_e_edicao_de_usuario_ti(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        admin = self.app_services.authenticate_user("admin@example.com", "Senha123")
        _, errors = self.app_services.create_user(
            {
                "name": "Suporte 1",
                "email": "suporte@example.com",
                "department": "TI",
                "password": "Senha456",
            },
            current_user_id=admin["id"],
        )
        self.assertFalse(errors)
        users = self.app_services.list_ti_users()
        suporte = next(row for row in users if row["email"] == "suporte@example.com")
        success, message = self.app_services.update_user(
            suporte["id"],
            {
                "name": "Suporte Interno",
                "email": "suporte@example.com",
                "department": "Service Desk",
                "password": "",
                "is_active": "1",
            },
            admin["id"],
        )
        self.assertTrue(success, message)

    def test_exclusao_de_usuario_remove_acesso_mas_preserva_registro(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin-excluir@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        admin = self.app_services.authenticate_user("admin-excluir@example.com", "Senha123")
        _, errors = self.app_services.create_user(
            {
                "name": "Suporte Descartavel",
                "email": "descartavel@example.com",
                "department": "TI",
                "password": "Senha456",
            },
            current_user_id=admin["id"],
        )
        self.assertFalse(errors)
        alvo = next(row for row in self.app_services.list_ti_users() if row["email"] == "descartavel@example.com")

        success, message = self.app_services.delete_user(alvo["id"], admin["id"])
        self.assertTrue(success, message)

        self.assertFalse(any(row["id"] == alvo["id"] for row in self.app_services.list_ti_users()))
        self.assertIsNone(self.app_services.authenticate_user("descartavel@example.com", "Senha456"))

    def test_exclusao_de_usuario_bloqueia_autoexclusao_e_ultimo_ativo(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Unico Admin",
                "email": "unico@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        admin = self.app_services.authenticate_user("unico@example.com", "Senha123")

        success, message = self.app_services.delete_user(admin["id"], admin["id"])
        self.assertFalse(success)
        self.assertIn("próprio usuário", message)

        _, errors = self.app_services.create_user(
            {
                "name": "Agente Beta",
                "email": "beta@example.com",
                "department": "Beta",
                "role": "beta",
                "password": "Senha456",
            },
            current_user_id=admin["id"],
        )
        self.assertFalse(errors)
        agente_beta = next(row for row in self.app_services.list_ti_users() if row["email"] == "beta@example.com")

        success, message = self.app_services.delete_user(admin["id"], agente_beta["id"])
        self.assertFalse(success)
        self.assertIn("último usuário ativo do DP", message)

    def test_dashboard_publico_retorna_andamento_e_finalizados_com_operador(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Operador TI",
                "email": "operador@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        operador = self.app_services.authenticate_user("operador@example.com", "Senha123")

        _, _, andamento_id = self.app_services.create_ticket(
            {
                "requester_name": "Ana Souza",
                "category_id": "1",
                "company_name": "Alfa SPE 1",
                "request_category": "Folha mensal",
                "title": "Automacao de planilha",
                "description": "Precisamos automatizar o fechamento da planilha de conferencia.",
            }
        )
        _, _, concluido_id = self.app_services.create_ticket(
            {
                "requester_name": "Bruno Lima",
                "category_id": "2",
                "company_name": "Beta Filial",
                "request_category": "Férias",
                "vacation_subcategory": "Recibo de férias",
                "vacation_employee_name": "Bruno Lima",
                "vacation_start_date": "2026-08-03",
                "vacation_days": "20",
                "vacation_has_bonus": "Não",
            }
        )

        self.app_services.update_ticket(
            andamento_id,
            {
                "status": "Em atendimento",
                "internal_note": "Fluxo iniciado",
                "comment": "Atendimento em execucao.",
                "assign_to_me": "1",
            },
            operador["id"],
        )
        self.app_services.update_ticket(
            concluido_id,
            {
                "status": "Concluido",
                "internal_note": "Chamado resolvido",
                "comment": "Entrega concluida.",
                "assign_to_me": "1",
            },
            operador["id"],
        )

        andamento, concluidos = self.app_services.get_public_dashboard_snapshot()

        self.assertTrue(any(row["id"] == andamento_id for row in andamento))
        self.assertTrue(any(row["id"] == concluido_id for row in concluidos))
        andamento_row = next(row for row in andamento if row["id"] == andamento_id)
        concluido_row = next(row for row in concluidos if row["id"] == concluido_id)
        self.assertEqual(andamento_row["assigned_name"], "Operador TI")
        self.assertEqual(concluido_row["assigned_name"], "Operador TI")

    def test_busca_do_painel_encontra_admissao_por_cpf_do_funcionario(self) -> None:
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Admissão",
                "employee_name": "Maria da Silva",
                "employee_cpf": "123.456.789-01",
                "employee_birth_date": "1990-01-10",
                "employee_role": "Auxiliar administrativo",
                "employee_cbo": "4110-05",
                "employee_admission_date": "2026-05-20",
                "employee_salary": "2500,00",
                "employee_work_schedule": "Seg a sex 08:00 as 17:48",
                "employee_experience_contract": "30/60",
                "employee_insalubrity": "Não",
                "employee_hazard_pay": "Não",
                "employee_transport_voucher": "Sim",
                "employee_meal_voucher": "Sim",
                "employee_ethnicity": "Branco",
                "employee_gender": "Feminino",
                "employee_education": "Ensino médio Completo",
                "employee_zip_code": "90000000",
                "employee_address": "Rua Exemplo",
                "employee_address_number": "123",
                "employee_city": "Porto Alegre",
                "employee_neighborhood": "Centro",
                "employee_state": "RS",
                "employee_phone": "(51) 99999-0003",
                "employee_mother_name": "Joana da Silva",
                "employee_birth_city": "Porto Alegre",
                "employee_birth_state": "RS",
                "employee_rg_issue_date": "2015-01-10",
                "employee_work_card_type": "Digital",
                "employee_department": "Administrativo",
                "employee_cost_center": "ADM-01",
                "employee_advance_enabled": "Não",
                "employee_marital_status": "Solteiro",
                "employee_has_alimony": "Não",
                "employee_bank_name": "Banco Teste",
                "employee_bank_account_type": "Corrente",
                "employee_bank_account_number": "12345-6",
                "employee_bank_agency": "0001",
                "employee_pix_key": "12345678901",
                "employee_pix_key_type": "CPF",
            }
        )
        self.assertIsNotNone(ticket_id)
        tickets, _, _ = self.app_services.get_dashboard_data(
            "todos",
            {"category_id": "", "assigned_to": "", "q": "12345678901", "sort": "recentes"},
        )
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["id"], ticket_id)

    def test_exportacao_csv_de_admissao_preserva_cabecalho_e_aplica_regras(self) -> None:
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Teste Operacional",
                "category_id": "1",
                "company_name": "Alfa SPE 1",
                "request_category": "Admissão",
                "employee_name": "Ana Paula Teste",
                "employee_cpf": "123.456.789-01",
                "employee_birth_date": "1996-08-14",
                "employee_role": "Assistente administrativo",
                "employee_cbo": "4110-10",
                "employee_department": "Administrativo",
                "employee_cost_center": "ADM-01",
                "employee_admission_date": "2026-05-20",
                "employee_salary": "2850,00",
                "employee_work_schedule": "Seg a sex 08:00 as 17:48",
                "employee_experience_contract": "30/60",
                "employee_insalubrity": "Não",
                "employee_hazard_pay": "Não",
                "employee_transport_voucher": "Sim",
                "employee_meal_voucher": "Sim",
                "employee_ethnicity": "Pardo",
                "employee_gender": "Feminino",
                "employee_education": "Superior Incompleto",
                "employee_zip_code": "90000-000",
                "employee_address": "Rua de Teste",
                "employee_address_number": "123",
                "employee_city": "Porto Alegre",
                "employee_neighborhood": "Centro",
                "employee_state": "rs",
                "employee_phone": "(51) 99999-1234",
                "employee_father_name": "Carlos Teste",
                "employee_mother_name": "Marcia Teste",
                "employee_birth_city": "Porto Alegre",
                "employee_birth_state": "rs",
                "employee_marital_status": "Solteiro",
                "employee_pis": "123.45678.90-1",
                "employee_rg": "10.203.405-6",
                "employee_rg_issuer": "SSP",
                "employee_rg_issuer_state": "rs",
                "employee_rg_issue_date": "2014-03-10",
                "employee_work_card_type": "Digital",
                "employee_voter_id": "123456789012",
                "employee_voter_zone": "114",
                "employee_voter_section": "0221",
                "employee_advance_enabled": "Não",
                "employee_has_alimony": "Não",
                "employee_bank_name": "Banco Teste",
                "employee_bank_account_type": "Corrente",
                "employee_bank_account_number": "12345-6",
                "employee_bank_agency": "0001",
                "employee_pix_key": "12345678901",
                "employee_pix_key_type": "CPF",
            }
        )
        self.assertIsNotNone(ticket_id)
        file_name, body = self.app_services.build_admission_csv_export(ticket_id)
        self.assertTrue(file_name.endswith(".csv"))
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(len(rows), 2)
        header, data = rows
        self.assertEqual(header[0], "Sigla")
        self.assertEqual(header[1], "Filial")
        self.assertEqual(header[2], "Codigo funcionario")
        row = dict(zip(header, data))
        self.assertEqual(row["Sigla"], "S22")
        self.assertEqual(row["Filial"], "")
        self.assertEqual(row["Codigo funcionario"], "")
        self.assertEqual(row["Cpf"], "12345678901")
        self.assertEqual(row["Nome"], "Ana Paula Teste")
        self.assertEqual(row["Data admissao"], "20/05/2026")
        self.assertEqual(row["Data nascimento"], "14/08/1996")
        self.assertEqual(row["Pais nascimento"], "Brasil")
        self.assertEqual(row["Nacionalidade"], "Brasil")
        self.assertEqual(row["Pais nacionalidade"], "Brasil")
        self.assertEqual(row["Estado"], "RS")
        self.assertEqual(row["Telefone"], "51999991234")
        self.assertEqual(row["PIS/PASEP"], "12345678901")
        self.assertEqual(row["Numero identidade"], "102034056")
        self.assertEqual(row["Dias contrato experiencia"], "30")
        self.assertEqual(row["Dias prorrogacao contrato"], "60")
        self.assertEqual(row["CTPS digital"], "Sim")
        self.assertEqual(row["Horas semanais"], "")
        self.assertEqual(row["Foto"], "")

    def test_dashboard_publico_considera_status_em_analise_e_aguardando_retorno_como_andamento(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Operador TI",
                "email": "operador@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        operador = self.app_services.authenticate_user("operador@example.com", "Senha123")

        _, _, em_analise_id = self.app_services.create_ticket(
            {
                "requester_name": "Julia",
                "category_id": "1",
                "company_name": "Alfa SPE 2",
                "request_category": "Rescisão",
                "rescission_subcategory": "Cálculo de rescisão",
                "rescission_type": "Pedido de demissão",
                "rescission_date": "2026-10-30",
                "title": "Ticket em análise",
                "description": "Descrição suficiente para validar o status em análise.",
            }
        )
        _, _, aguardando_id = self.app_services.create_ticket(
            {
                "requester_name": "Renato",
                "category_id": "2",
                "company_name": "Beta",
                "request_category": "Admissão",
                "title": "Ticket aguardando retorno",
                "description": "Descrição suficiente para validar o status aguardando retorno.",
            }
        )

        self.app_services.update_ticket(
            em_analise_id,
            {"status": "Em analise", "internal_note": "Triagem interna", "comment": "", "assign_to_me": "1"},
            operador["id"],
        )
        self.app_services.update_ticket(
            aguardando_id,
            {
                "status": "Aguardando retorno",
                "internal_note": "Aguardando resposta",
                "comment": "Precisamos da sua confirmacao.",
                "assign_to_me": "1",
            },
            operador["id"],
        )

        andamento, _ = self.app_services.get_public_dashboard_snapshot()
        andamento_ids = {row["id"] for row in andamento}
        self.assertIn(em_analise_id, andamento_ids)
        self.assertIn(aguardando_id, andamento_ids)

    def test_render_dashboard_concluidos_com_admissao_nao_quebra(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Agente DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        dp_user = self.app_services.authenticate_user("dp@example.com", "Senha123")

        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Teste Operacional",
                "category_id": "1",
                "company_name": "Alfa SPE 1",
                "request_category": "Admissão",
                "employee_name": "Ana Paula Teste",
                "employee_cpf": "12345678901",
                "employee_birth_date": "1996-08-14",
                "employee_role": "Assistente administrativo",
                "employee_cbo": "4110-10",
                "employee_admission_date": "2026-05-20",
                "employee_salary": "2850,00",
                "employee_work_schedule": "Seg a sex 08:00 as 17:48",
                "employee_experience_contract": "30/60",
                "employee_insalubrity": "Não",
                "employee_hazard_pay": "Não",
                "employee_transport_voucher": "Sim",
                "employee_meal_voucher": "Sim",
                "employee_ethnicity": "Pardo",
                "employee_gender": "Feminino",
                "employee_education": "Superior Incompleto",
                "employee_zip_code": "90000000",
                "employee_address": "Rua de Teste",
                "employee_address_number": "123",
                "employee_city": "Porto Alegre",
                "employee_neighborhood": "Centro",
                "employee_state": "RS",
                "employee_phone": "(51) 99999-1234",
                "employee_mother_name": "Marcia Teste",
                "employee_birth_city": "Porto Alegre",
                "employee_birth_state": "RS",
                "employee_rg_issue_date": "2014-03-10",
                "employee_work_card_type": "Digital",
                "employee_department": "Administrativo",
                "employee_cost_center": "ADM-01",
                "employee_advance_enabled": "Não",
                "employee_marital_status": "Solteiro",
                "employee_has_alimony": "Não",
                "employee_bank_name": "Banco Teste",
                "employee_bank_account_type": "Corrente",
                "employee_bank_account_number": "12345-6",
                "employee_bank_agency": "0001",
                "employee_pix_key": "12345678901",
                "employee_pix_key_type": "CPF",
                "dependent_0_name": "Dependente Teste",
                "dependent_0_cpf": "98765432100",
                "dependent_0_birth_date": "2018-02-03",
                "dependent_0_relationship": "Filho(a) ou enteado(a)",
                "dependent_0_ir_dependent": "Sim",
            }
        )
        self.app_services.update_ticket(
            ticket_id,
            {"status": "Concluido", "comment": "Finalizado.", "assign_to_me": "1"},
            dp_user["id"],
        )

        filters = {"category_id": "", "assigned_to": "", "q": "", "sort": "recentes"}
        tickets, summary, events_by_ticket = self.app_services.get_dashboard_data("concluidos", filters)
        payload = self.server_module.render_ti_dashboard(
            dp_user,
            tickets,
            events_by_ticket,
            summary,
            "concluidos",
            "csrf-token",
            self.app_services.list_categories(),
            self.app_services.list_ti_users(active_only=True),
            filters,
        )

        self.assertIn("Dependente 1", payload)
        self.assertIn("Finalizado.", payload)
        self.assertIn("Concluídas", payload)

    def test_confirmacao_de_chamado_sem_email_e_ignorada(self) -> None:
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Carlos Mendes",
                "category_id": "2",
                "company_name": "Beta Filial",
                "request_category": "Folha mensal",
                "title": "Teste de confirmacao",
                "description": "Abrindo um chamado para validar a geracao da confirmacao por e-mail.",
            }
        )

        delivery_mode = self.app_services.send_ticket_confirmation(ticket_id)

        self.assertEqual(delivery_mode, "skipped")
        outbox_dir = Path(os.environ["APP_OUTBOX_PATH"])
        files = list(outbox_dir.glob("*.eml"))
        self.assertEqual(len(files), 0)

    def test_resposta_ao_solicitante_fica_visivel_na_consulta_publica_sem_expor_resumo_interno(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Paula Lima",
                "category_id": "2",
                "company_name": "Beta",
                "request_category": "Férias",
                "vacation_subcategory": "Recibo de férias",
                "vacation_employee_name": "Paula Lima",
                "vacation_start_date": "2026-09-09",
                "vacation_days": "30",
                "vacation_has_bonus": "Sim",
                "vacation_bonus_days": "10",
            }
        )

        success, message = self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Aguardando retorno",
                "internal_note": "Aguardando confirmacao do gestor para liberar perfil.",
                "comment": "Precisamos da confirmacao do seu gestor para liberar o acesso.",
                "assign_to_me": "1",
            },
            user["id"],
        )

        self.assertTrue(success, message)
        events = self.app_services.get_public_ticket_events(ticket_id)
        messages = [row["message"] for row in events]
        self.assertIn("Precisamos da confirmacao do seu gestor para liberar o acesso.", messages)
        self.assertNotIn("Resumo interno atualizado: Aguardando confirmacao do gestor para liberar perfil.", messages)

    def test_atualizacao_de_chamado_sem_email_nao_gera_notificacao(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Joao Pedro",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Admissão",
                "title": "Automacao de relatorio",
                "description": "Precisamos automatizar a consolidacao do relatorio semanal.",
            }
        )

        outbox_dir = Path(os.environ["APP_OUTBOX_PATH"])
        self.app_services.send_ticket_confirmation(ticket_id)
        before_files = set(outbox_dir.glob("*.eml"))

        success, message = self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Em atendimento",
                "internal_note": "Analise interna iniciada.",
                "comment": "Já iniciamos a análise da sua solicitação.",
                "assign_to_me": "1",
            },
            user["id"],
        )

        self.assertTrue(success, message)
        after_files = set(outbox_dir.glob("*.eml"))
        new_files = list(after_files - before_files)
        self.assertEqual(len(new_files), 0)

    def test_exclusao_de_chamado_remove_registro_e_historico(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Julia Lopes",
                "category_id": "2",
                "company_name": "Beta Filial",
                "request_category": "Rescisão",
                "rescission_subcategory": "Aviso prévio",
                "rescission_type": "Pedido de demissão",
                "rescission_date": "2026-10-30",
                "title": "Excluir ticket",
                "description": "Chamado criado para validar a exclusao.",
            }
        )
        self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Em atendimento",
                "comment": "Atendimento iniciado.",
                "assign_to_me": "1",
            },
            user["id"],
        )

        success, message = self.app_services.delete_ticket(ticket_id)

        self.assertTrue(success, message)
        self.assertIsNone(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertEqual(self.app_services.get_public_ticket_events(ticket_id), [])

    def test_backup_automatico_gera_copia_sqlite_consistente(self) -> None:
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Marina Costa",
                "category_id": "1",
                "company_name": "Alfa SPE 1",
                "request_category": "Folha mensal",
                "title": "Backup de teste",
                "description": "Validando a geracao de backup consistente do banco.",
            }
        )

        backup_path = Path(self.app_services.make_backup(test=True))

        self.assertTrue(backup_path.exists())

        import sqlite3

        with sqlite3.connect(backup_path) as conn:
            row = conn.execute("SELECT id, title FROM tickets WHERE id = ?", (ticket_id,)).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[1], "Backup de teste")

    def test_backup_em_postgres_retorna_skip(self) -> None:
        with mock.patch.object(self.app_services, "DB_BACKEND", "postgres"):
            self.assertEqual(self.app_services.make_backup(test=True), "skipped:postgres")

    def test_criacao_de_chamado_com_anexo_persiste_arquivo_e_metadados(self) -> None:
        attachment = {
            "filename": "holerite.pdf",
            "content_type": "application/pdf",
            "content": b"pdf-bytes",
            "size_bytes": len(b"pdf-bytes"),
        }
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Patricia Lima",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Envio com anexo",
                "description": "Chamado criado para validar persistencia do anexo.",
            },
            attachment,
        )

        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)
        attachments = self.app_services.list_ticket_attachments(ticket_id)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["original_name"], "holerite.pdf")

        attachment_row, attachment_path = self.app_services.get_ticket_attachment(attachments[0]["id"])
        self.assertIsNotNone(attachment_row)
        self.assertTrue(attachment_path.exists())

    def test_pg_conn_shim_traduz_insert_para_lastrowid(self) -> None:
        class FakeCursor:
            def __init__(self) -> None:
                self.rowcount = 1
                self.statements: list[tuple[str, tuple]] = []

            def execute(self, sql, params=()):
                self.statements.append((sql, tuple(params)))

            def fetchone(self):
                return {"id": 99}

            def fetchall(self):
                return []

            def __iter__(self):
                return iter(())

        class FakeConn:
            def __init__(self) -> None:
                self.cursor_instance = FakeCursor()
                self.rollbacks = 0

            def cursor(self):
                return self.cursor_instance

            def rollback(self):
                self.rollbacks += 1

        fake_conn = FakeConn()
        self.app_db._psycopg = types.SimpleNamespace(errors=types.SimpleNamespace(UndefinedColumn=RuntimeError, IntegrityError=RuntimeError))

        cursor = self.app_db._PgConnShim(fake_conn).execute("INSERT INTO tickets (title) VALUES (?)", ("Teste",))

        self.assertEqual(cursor.lastrowid, 99)
        self.assertEqual(fake_conn.cursor_instance.statements[0][0], "INSERT INTO tickets (title) VALUES (%s) RETURNING id")

    def test_ensure_schema_em_postgres_aplica_migracoes_pendentes(self) -> None:
        executed: list[tuple[str, tuple | None]] = []

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class FakeConn:
            def __init__(self) -> None:
                self.row_factory = None

            def execute(self, sql, params=None):
                executed.append((sql, tuple(params) if params is not None else None))
                if "SELECT version FROM schema_migrations" in sql:
                    return FakeResult([])
                return FakeResult([])

            def commit(self):
                executed.append(("COMMIT", None))

        class FakePoolConnection:
            def __enter__(self):
                return FakeConn()

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakePool:
            def connection(self):
                return FakePoolConnection()

        fake_rows_module = types.ModuleType("psycopg.rows")
        fake_rows_module.dict_row = object()
        with mock.patch.dict(sys.modules, {"psycopg.rows": fake_rows_module}):
            with mock.patch.object(self.app_db, "DB_BACKEND", "postgres"):
                with mock.patch.object(self.app_db, "_get_pg_pool", return_value=FakePool()):
                    self.app_db.ensure_schema()

        executed_sql = [item[0] for item in executed]
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS schema_migrations" in sql for sql in executed_sql))
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS request_types" in sql for sql in executed_sql))
        self.assertTrue(any(sql == "COMMIT" for sql in executed_sql))

    def test_parse_multipart_form_data_sem_cgi_preserva_campos_e_anexo(self) -> None:
        boundary = "----AlfaBoundary7"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="requester_name"\r\n\r\n'
            "Maria Silva\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="title"\r\n\r\n'
            "Atualização de folha\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="attachment"; filename="holerite.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
            "pdf-bytes\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")

        form, attachment = self.server_module.parse_multipart_form_data(
            f"multipart/form-data; boundary={boundary}",
            body,
        )

        self.assertEqual(form["requester_name"], "Maria Silva")
        self.assertEqual(form["title"], "Atualização de folha")
        self.assertIsNotNone(attachment)
        self.assertEqual(attachment["filename"], "holerite.pdf")
        self.assertEqual(attachment["content_type"], "application/pdf")
        self.assertEqual(attachment["content"], b"pdf-bytes")
        self.assertEqual(attachment["size_bytes"], len(b"pdf-bytes"))

    def test_criacao_de_solicitacao_recria_schema_legado_de_categoria_e_anexos(self) -> None:
        import sqlite3

        legacy_db = Path(os.environ["APP_DB_PATH"])
        if legacy_db.exists():
            legacy_db.unlink()

        conn = sqlite3.connect(legacy_db)
        conn.executescript((PROJECT_ROOT / "db/migrations/001_initial.sql").read_text(encoding="utf-8"))
        conn.executescript((PROJECT_ROOT / "db/migrations/002_upgrade.sql").read_text(encoding="utf-8"))
        conn.executescript((PROJECT_ROOT / "db/migrations/003_cleanup_legacy_schema.sql").read_text(encoding="utf-8"))
        conn.executescript((PROJECT_ROOT / "db/migrations/004_login_rate_limit_store.sql").read_text(encoding="utf-8"))
        conn.executescript((PROJECT_ROOT / "db/migrations/008_ticket_company_name.sql").read_text(encoding="utf-8"))
        conn.executescript((PROJECT_ROOT / "db/migrations/009_user_roles_and_invites.sql").read_text(encoding="utf-8"))
        conn.commit()
        conn.close()

        import importlib

        importlib.reload(self.app_db)
        importlib.reload(self.app_services)

        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Larissa Gomes",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Schema legado",
                "description": "Solicitação criada para validar autoajuste do schema legado.",
            }
        )

        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)

        with self.app_db.get_db() as conn:
            ticket_columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
            attachments_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ticket_attachments'"
            ).fetchone()
            events_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ticket_events'"
            ).fetchone()
            row = conn.execute("SELECT request_category, public_token, updated_at FROM tickets WHERE id = ?", (ticket_id,)).fetchone()

        self.assertIn("request_category", ticket_columns)
        self.assertIn("public_token", ticket_columns)
        self.assertIn("updated_at", ticket_columns)
        self.assertIsNotNone(attachments_table)
        self.assertIsNotNone(events_table)
        self.assertEqual(row[0], "Folha mensal")
        self.assertTrue(row[1])
        self.assertTrue(row[2])

    def test_schema_limpo_remove_legado_do_fluxo_oficial(self) -> None:
        import sqlite3

        with sqlite3.connect(os.environ["APP_DB_PATH"]) as conn:
            ticket_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(tickets)").fetchall()
            }
            activities_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'activities'"
            ).fetchone()
            attachments_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ticket_attachments'"
            ).fetchone()

        self.assertNotIn("requester_phone", ticket_columns)
        self.assertIn("company_name", ticket_columns)
        self.assertIn("request_category", ticket_columns)
        self.assertIn("public_token", ticket_columns)
        self.assertIsNone(activities_table)
        self.assertIsNotNone(attachments_table)

    def test_rate_limit_de_login_fica_persistido_no_banco(self) -> None:
        self.assertFalse(self.app_services.is_login_rate_limited("127.0.0.1"))
        for _ in range(self.app_config.LOGIN_RATE_LIMIT_ATTEMPTS):
            self.app_services.register_login_attempt("127.0.0.1", success=False)
        self.assertTrue(self.app_services.is_login_rate_limited("127.0.0.1"))

    def test_rate_limit_de_login_recria_tabela_quando_ela_nao_existe(self) -> None:
        with self.app_db.get_db() as conn:
            conn.execute("DROP TABLE IF EXISTS login_attempts")

        self.assertFalse(self.app_services.is_login_rate_limited("127.0.0.1"))
        self.app_services.register_login_attempt("127.0.0.1", success=False)
        self.assertFalse(self.app_services.is_login_rate_limited("127.0.0.1"))

        with self.app_db.get_db() as conn:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'login_attempts'"
            ).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]

        self.assertIsNotNone(table)
        self.assertEqual(count, 1)

    def test_parse_client_ip_confia_em_x_forwarded_for_so_para_proxy_confiavel(self) -> None:
        handler = types.SimpleNamespace(
            headers={"X-Forwarded-For": "198.51.100.24, 127.0.0.1"},
            client_address=("127.0.0.1", 8001),
        )

        self.assertEqual(self.app_services.parse_client_ip(handler), "198.51.100.24")

    def test_parse_client_ip_ignora_x_forwarded_for_forjado_em_acesso_direto(self) -> None:
        handler = types.SimpleNamespace(
            headers={"X-Forwarded-For": "198.51.100.24"},
            client_address=("203.0.113.10", 51234),
        )

        self.assertEqual(self.app_services.parse_client_ip(handler), "203.0.113.10")

    def test_criacao_de_sessao_recria_coluna_updated_at_quando_ela_nao_existe(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Equipe DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        user = self.app_services.authenticate_user("dp@example.com", "Senha123")

        with self.app_db.get_db() as conn:
            conn.execute("DROP TABLE sessions")
            conn.execute(
                """
                CREATE TABLE sessions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  token TEXT NOT NULL UNIQUE,
                  expires_at TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )

        token = self.app_services.create_session(user["id"])
        self.assertTrue(token)

        with self.app_db.get_db() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            session = conn.execute(
                "SELECT updated_at FROM sessions WHERE token = ?",
                (token,),
            ).fetchone()

        self.assertIn("updated_at", columns)
        self.assertIsNotNone(session)
        self.assertTrue(session[0])

    def test_get_db_postgres_recupera_conexao_quebrada_do_pool(self) -> None:
        original_backend = self.app_db.DB_BACKEND
        original_pool_factory = self.app_db._get_pg_pool
        self.app_db.DB_BACKEND = "postgres"

        class FakeConn:
            def __init__(self, *, fail_probe: bool = False) -> None:
                self.fail_probe = fail_probe
                self.autocommit = True
                self.row_factory = None
                self.closed = False
                self.probes: list[str] = []

            def execute(self, sql: str):
                self.probes.append(sql)
                if self.fail_probe:
                    raise RuntimeError("stale connection")
                return None

            def close(self) -> None:
                self.closed = True

        class FakePool:
            def __init__(self) -> None:
                self.first = FakeConn(fail_probe=True)
                self.second = FakeConn()
                self._connections = [self.first, self.second]
                self.returned: list[FakeConn] = []

            def getconn(self):
                return self._connections.pop(0)

            def putconn(self, conn) -> None:
                self.returned.append(conn)

        pool = FakePool()
        self.app_db._get_pg_pool = lambda: pool
        try:
            conn = self.app_db.get_db()
            self.assertIs(conn._conn, pool.second)
            self.assertEqual(pool.first.probes, ["SELECT 1"])
            self.assertTrue(pool.first.closed)
            self.assertEqual(pool.second.probes, ["SELECT 1"])
            self.assertFalse(pool.second.autocommit)
        finally:
            self.app_db.DB_BACKEND = original_backend
            self.app_db._get_pg_pool = original_pool_factory

    def test_pg_conn_shim_nao_mascara_erro_original_quando_rollback_falha(self) -> None:
        original_pool_factory = self.app_db._get_pg_pool

        class FakeConn:
            def __init__(self) -> None:
                self.closed = False

            def rollback(self) -> None:
                raise RuntimeError("rollback failed")

            def close(self) -> None:
                self.closed = True

        class FakePool:
            def putconn(self, conn) -> None:
                raise AssertionError("conexao quebrada nao deve voltar ao pool")

        conn = FakeConn()
        shim = self.app_db._PgConnShim(conn)
        self.app_db._get_pg_pool = lambda: FakePool()
        try:
            shim.__exit__(RuntimeError, RuntimeError("query failed"), None)
            self.assertTrue(conn.closed)
        finally:
            self.app_db._get_pg_pool = original_pool_factory

    def test_public_ticket_page_renderiza_interacoes_com_papeis_distintos(self) -> None:
        ticket = {
            "id": 42,
            "status": "Aguardando retorno",
            "category_name": "Alfa",
            "company_name": "Empresa Teste",
            "request_category": "Folha mensal",
            "created_at": "2026-05-28T10:00:00+00:00",
            "updated_at": "2026-05-28T12:00:00+00:00",
            "assigned_name": "Equipe DP",
            "title": "Ajuste de folha",
            "public_token": "token-publico",
            "description": "Descricao",
            "admission_payload": "",
        }
        events = [
            {
                "created_at": "2026-05-28T12:00:00+00:00",
                "event_type": "comentario",
                "message": "Precisamos do comprovante atualizado.",
            },
            {
                "created_at": "2026-05-28T12:10:00+00:00",
                "event_type": "solicitante",
                "message": "Segue retorno com a informacao.",
            },
            {
                "created_at": "2026-05-28T11:00:00+00:00",
                "event_type": "status",
                "message": "Status alterado de Aberto para Aguardando retorno.",
            },
        ]

        payload = self.server_module.public_ticket_page(ticket, events, [], "csrf-token", can_comment=True)

        self.assertIn("Atualizações compartilhadas", payload)
        self.assertIn("Departamento Pessoal", payload)
        self.assertIn("Empresa", payload)
        self.assertIn("interaction-card--dp", payload)
        self.assertIn("interaction-card--requester", payload)
        self.assertIn("Resposta do DP", payload)
        self.assertIn("Retorno da empresa", payload)
        self.assertIn("Enviar mensagem", payload)
        self.assertIn("Anexar arquivo", payload)
        self.assertIn("Você pode enviar só a mensagem, só o anexo, ou os dois juntos.", payload)
        self.assertNotIn("Comentários da solicitação", payload)

    def test_solicitante_pode_comentar_pelo_link_publico_sem_login(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Erro em rotina mensal",
                "description": "A rotina mensal falhou ao consolidar os dados do fechamento.",
            }
        )
        self.assertFalse(errors)
        public_token = self.app_services.get_public_ticket_token_by_id(ticket_id)

        success, message = self.app_services.add_requester_comment(
            public_token,
            None,
            "Segue o retorno solicitado.",
            {
                "filename": "retorno.pdf",
                "content": b"arquivo teste",
                "content_type": "application/pdf",
                "size_bytes": 13,
            },
        )

        self.assertTrue(success)
        self.assertEqual(message, "Comentário enviado.")
        events = self.app_services.get_public_ticket_events(ticket_id)
        self.assertTrue(any("Solicitante: Segue o retorno solicitado. Anexo: retorno.pdf" == row["message"] for row in events))
        attachments = self.app_services.list_ticket_attachments(ticket_id)
        self.assertEqual(len(attachments), 1)

    def test_dp_pode_atualizar_ticket_com_anexo(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Equipe DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        dp_user = self.app_services.authenticate_user("dp@example.com", "Senha123")
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Erro em rotina mensal",
                "description": "A rotina mensal falhou ao consolidar os dados do fechamento.",
            }
        )
        self.assertFalse(errors)

        success, message = self.app_services.update_ticket(
            ticket_id,
            {"status": "Em atendimento", "comment": "Segue orientação do DP."},
            dp_user["id"],
            {
                "filename": "orientacao.pdf",
                "content": b"arquivo teste",
                "content_type": "application/pdf",
                "size_bytes": 13,
            },
        )

        self.assertTrue(success)
        self.assertEqual(message, "Chamado atualizado.")
        events = self.app_services.get_public_ticket_events(ticket_id)
        self.assertTrue(any("Segue orientação do DP. Anexo: orientacao.pdf" == row["message"] for row in events))
        attachments = self.app_services.list_ticket_attachments(ticket_id)
        self.assertEqual(len(attachments), 1)

    def test_dp_pode_designar_atendimento_para_outro_agente(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Agente Um",
                "email": "dp1@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        _, errors = self.app_services.create_user(
            {
                "name": "Agente Dois",
                "email": "dp2@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            }
        )
        self.assertFalse(errors)
        agente_um = self.app_services.authenticate_user("dp1@example.com", "Senha123")
        agente_dois = self.app_services.authenticate_user("dp2@example.com", "Senha123")
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Erro em rotina mensal",
                "description": "A rotina mensal falhou ao consolidar os dados do fechamento.",
            }
        )
        self.assertFalse(errors)

        success, message = self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Aberto",
                "comment": "",
                "assign_selected": "1",
                "assign_to_user_id": str(agente_dois["id"]),
            },
            agente_um["id"],
        )

        self.assertTrue(success)
        self.assertEqual(message, "Chamado atualizado.")
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertEqual(ticket["assigned_name"], "Agente Dois")

    def test_http_get_home_retorna_dashboard_em_andamento(self) -> None:
        status, _, headers, _ = self.request_http("GET", "/")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/dp/login")

    def test_head_healthz_retorna_headers_sem_corpo(self) -> None:
        status, payload, headers, _ = self.request_http("HEAD", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload, "")
        self.assertEqual(headers.get("Content-Type"), "application/json; charset=utf-8")
        self.assertTrue(int(headers.get("Content-Length", "0")) > 0)

    def test_head_home_retorna_redirect_sem_corpo(self) -> None:
        status, payload, headers, _ = self.request_http("HEAD", "/")
        self.assertEqual(status, 302)
        self.assertEqual(payload, "")
        self.assertEqual(headers.get("Location"), "/dp/login")
        self.assertEqual(headers.get("Content-Length"), "0")

    def test_usuario_empresa_nao_acessa_painel_dp(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Usuario Empresa",
                "email": "empresa@example.com",
                "department": "Empresa",
                "role": "company",
                "password": "Senha123",
            }
        )
        status_get, _, _, set_cookies = self.request_http("GET", "/ti/login")
        self.assertEqual(status_get, 200)
        csrf_cookie = next(value for value in set_cookies if value.startswith("helpdesk_csrf="))
        csrf_token = csrf_cookie.split(";", 1)[0].split("=", 1)[1]
        body = f"csrf_token={csrf_token}&email=empresa%40example.com&password=Senha123"
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Cookie": csrf_cookie.split(";", 1)[0]}
        _, _, login_headers, login_set_cookies = self.request_http("POST", "/ti/login", body=body, headers=headers)
        session_cookies = "; ".join(cookie.split(";", 1)[0] for cookie in login_set_cookies)
        status, _, panel_headers, _ = self.request_http("GET", "/ti", headers={"Cookie": session_cookies})
        self.assertEqual(status, 302)
        self.assertEqual(panel_headers.get("Location"), "/")

    def test_usuario_beta_ve_apenas_origem_beta_na_home(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Usuario Beta",
                "email": "beta@example.com",
                "department": "Beta",
                "role": "beta",
                "password": "Senha123",
            }
        )
        self.assertFalse(errors)
        user = self.app_services.authenticate_user("beta@example.com", "Senha123")
        token = self.app_services.create_session(user["id"])
        status, payload, _, _ = self.request_http("GET", "/", headers={"Cookie": f"helpdesk_session={token}"})
        self.assertEqual(status, 200)
        self.assertIn(">Beta<", payload)
        self.assertNotIn(">Alfa<", payload)

    def test_usuario_alfa_ve_apenas_origem_alfa_na_home(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Usuario Alfa",
                "email": "alfa@example.com",
                "department": "Alfa",
                "role": "alfa",
                "password": "Senha123",
            }
        )
        self.assertFalse(errors)
        user = self.app_services.authenticate_user("alfa@example.com", "Senha123")
        token = self.app_services.create_session(user["id"])
        status, payload, _, _ = self.request_http("GET", "/", headers={"Cookie": f"helpdesk_session={token}"})
        self.assertEqual(status, 200)
        self.assertIn(">Alfa<", payload)
        self.assertNotIn(">Beta<", payload)

    def test_usuario_ambos_ve_origens_beta_e_alfa_na_home(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Usuario Ambos",
                "email": "ambos@example.com",
                "department": "Beta/Alfa",
                "role": "ambos",
                "password": "Senha123",
            }
        )
        self.assertFalse(errors)
        user = self.app_services.authenticate_user("ambos@example.com", "Senha123")
        token = self.app_services.create_session(user["id"])
        status, payload, _, _ = self.request_http("GET", "/", headers={"Cookie": f"helpdesk_session={token}"})
        self.assertEqual(status, 200)
        self.assertIn(">Beta<", payload)
        self.assertIn(">Alfa<", payload)

    def test_painel_responsavel_lista_apenas_usuarios_dp(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Agente DP Responsavel",
                "email": "dp-responsavel@example.com",
                "department": "DP",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        dp_user = self.app_services.authenticate_user("dp-responsavel@example.com", "Senha123")
        _, errors = self.app_services.create_user(
            {
                "name": "Fulano Beta Nao Deve Aparecer",
                "email": "beta-nao-aparece@example.com",
                "department": "Beta",
                "role": "beta",
                "password": "Senha123",
            },
            current_user_id=dp_user["id"],
        )
        self.assertFalse(errors)
        token = self.app_services.create_session(dp_user["id"])
        status, payload, _, _ = self.request_http("GET", "/dp?tab=abertos", headers={"Cookie": f"helpdesk_session={token}"})
        self.assertEqual(status, 200)
        self.assertIn("Agente DP Responsavel", payload)
        self.assertNotIn("Fulano Beta Nao Deve Aparecer", payload)

    def test_tela_de_usuarios_nao_exibe_perfil_empresa_na_edicao(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Agente DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            }
        )
        dp_user = self.app_services.authenticate_user("dp@example.com", "Senha123")
        _, _ = self.app_services.create_user(
            {
                "name": "Usuario Legado",
                "email": "legado@example.com",
                "department": "Empresa",
                "role": "company",
                "password": "Senha123",
            }
        )

        payload = self.server_module.render_ti_users(dp_user, self.app_services.list_ti_users(), "csrf-token")

        self.assertNotIn('option value="company"', payload)
        self.assertIn("Adicionar agente", payload)
        self.assertIn("Agente DP", payload)
        self.assertIn("Beta", payload)
        self.assertIn("Alfa", payload)
        self.assertIn("Ambos", payload)

    def test_tela_de_usuarios_reabre_convite_com_dados_preservados_em_erro(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Agente DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            }
        )
        dp_user = self.app_services.authenticate_user("dp@example.com", "Senha123")

        payload = self.server_module.render_ti_users(
            dp_user,
            self.app_services.list_ti_users(),
            "csrf-token",
            invite_form_data={"name": "Us", "email": "convite@example.com", "role": "alfa"},
            invite_message="O nome deve ter pelo menos 3 caracteres.",
            invite_kind="error",
            invite_open=True,
        )

        self.assertIn('details class="invite-disclosure" open', payload)
        self.assertIn('value="Us"', payload)
        self.assertIn('value="convite@example.com"', payload)
        self.assertIn('option value="alfa" selected', payload)
        self.assertIn("O nome deve ter pelo menos 3 caracteres.", payload)

    def test_http_404_e_405_sao_padronizados(self) -> None:
        status_404, payload_404, _, _ = self.request_http("GET", "/nao-existe")
        status_405, payload_405, _, _ = self.request_http("PUT", "/")

        self.assertEqual(status_404, 404)
        self.assertIn("Página não encontrada", payload_404)
        self.assertEqual(status_405, 405)
        self.assertIn("Método não permitido", payload_405)

    def test_http_abertura_de_chamado_com_csrf_funciona_no_fluxo_real(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Usuario Empresa",
                "email": "empresa@example.com",
                "department": "Empresa",
                "role": "company",
                "password": "Senha123",
            }
        )
        status_login, _, _, login_page_cookies = self.request_http("GET", "/ti/login")
        self.assertEqual(status_login, 200)
        csrf_cookie = next(value for value in login_page_cookies if value.startswith("helpdesk_csrf="))
        csrf_token = csrf_cookie.split(";", 1)[0].split("=", 1)[1]
        login_body = f"csrf_token={csrf_token}&email=empresa%40example.com&password=Senha123"
        login_headers = {"Content-Type": "application/x-www-form-urlencoded", "Cookie": csrf_cookie.split(';', 1)[0]}
        status_auth, _, _, auth_cookies = self.request_http("POST", "/ti/login", body=login_body, headers=login_headers)
        self.assertEqual(status_auth, 302)
        auth_cookie_header = "; ".join(cookie.split(";", 1)[0] for cookie in auth_cookies)

        status_get, payload_get, _, _ = self.request_http("GET", "/novo-chamado?category_id=1", headers={"Cookie": auth_cookie_header})
        self.assertEqual(status_get, 200)
        self.assertIn("Nova solicitação de folha", payload_get)

        fresh_csrf_cookie = next(cookie for cookie in auth_cookies if cookie.startswith("helpdesk_csrf="))
        fresh_csrf_token = fresh_csrf_cookie.split(";", 1)[0].split("=", 1)[1]
        body = (
            f"csrf_token={fresh_csrf_token}&category_id=1&company_name=Alfa Matriz"
            "&request_category=Folha+mensal&title=Chamado+HTTP&description=Descrição+completa+do+chamado+via+HTTP"
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": auth_cookie_header,
        }

        status_post, payload_post, _, _ = self.request_http("POST", "/abrir-chamado", body=body, headers=headers)
        self.assertEqual(status_post, 200)
        self.assertIn("Solicitação enviada com sucesso", payload_post)

    def test_http_bloqueia_usuario_beta_de_abrir_alfa(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Usuario Beta",
                "email": "beta@example.com",
                "department": "Beta",
                "role": "beta",
                "password": "Senha123",
            }
        )
        self.assertFalse(errors)
        status_login, _, _, login_page_cookies = self.request_http("GET", "/ti/login")
        self.assertEqual(status_login, 200)
        csrf_cookie = next(value for value in login_page_cookies if value.startswith("helpdesk_csrf="))
        csrf_token = csrf_cookie.split(";", 1)[0].split("=", 1)[1]
        login_body = f"csrf_token={csrf_token}&email=beta%40example.com&password=Senha123"
        login_headers = {"Content-Type": "application/x-www-form-urlencoded", "Cookie": csrf_cookie.split(';', 1)[0]}
        status_auth, _, _, auth_cookies = self.request_http("POST", "/ti/login", body=login_body, headers=login_headers)
        self.assertEqual(status_auth, 302)
        auth_cookie_header = "; ".join(cookie.split(";", 1)[0] for cookie in auth_cookies)

        status_get, payload_get, _, _ = self.request_http("GET", "/novo-chamado?category_id=1", headers={"Cookie": auth_cookie_header})
        self.assertEqual(status_get, 200)
        self.assertIn("Você não tem acesso a essa origem de solicitação.", payload_get)

        fresh_csrf_cookie = next(cookie for cookie in auth_cookies if cookie.startswith("helpdesk_csrf="))
        fresh_csrf_token = fresh_csrf_cookie.split(";", 1)[0].split("=", 1)[1]
        body = (
            f"csrf_token={fresh_csrf_token}&category_id=1&company_name=Alfa Matriz"
            "&request_category=Folha+mensal&title=Chamado+HTTP&description=Descrição+completa+do+chamado+via+HTTP"
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": auth_cookie_header,
        }
        status_post, payload_post, _, _ = self.request_http("POST", "/abrir-chamado", body=body, headers=headers)
        self.assertEqual(status_post, 200)
        self.assertIn("Você não tem acesso a essa origem de solicitação.", payload_post)

    def test_ticket_cancelado_nao_aceita_resposta_pelo_link_publico(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Equipe DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        dp_user = self.app_services.authenticate_user("dp@example.com", "Senha123")
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Teste",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Erro em rotina mensal",
                "description": "A rotina mensal falhou ao consolidar os dados do fechamento.",
            }
        )
        self.assertFalse(errors)
        public_token = self.app_services.get_public_ticket_token_by_id(ticket_id)

        success, _ = self.app_services.update_ticket(
            ticket_id,
            {"status": "Cancelado", "comment": "Cancelado a pedido da empresa."},
            dp_user["id"],
        )
        self.assertTrue(success)

        success, message = self.app_services.add_requester_comment(
            public_token,
            {"id": 999, "role": "beta", "name": "Responsável Empresa"},
            "Ainda preciso disso.",
        )
        self.assertFalse(success)
        self.assertIn("cancelada", message.lower())

        ticket = self.app_services.get_public_ticket(public_token)
        payload = self.server_module.public_ticket_page(
            ticket,
            self.app_services.get_public_ticket_events(ticket_id),
            [],
            "csrf-token",
            can_comment=True,
            logged_in=True,
        )
        self.assertIn("Solicitação cancelada", payload)
        self.assertNotIn(f"/acompanhar/{public_token}/comentario", payload)

    def test_fluxo_redefinicao_de_senha(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Equipe DP",
                "email": "dp@example.com",
                "department": "DP",
                "role": "dp",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)

        self.assertIsNone(self.app_services.create_password_reset_request("naoexiste@example.com"))

        reset_id = self.app_services.create_password_reset_request("DP@example.com")
        self.assertIsNotNone(reset_id)
        reset = self.app_db.get_db().execute(
            "SELECT reset_token FROM password_resets WHERE id = ?", (reset_id,)
        ).fetchone()
        token = reset["reset_token"]
        self.assertIsNotNone(self.app_services.get_password_reset(token))

        ok, _ = self.app_services.reset_user_password(
            token, {"password": "abc", "confirm_password": "abc"}
        )
        self.assertFalse(ok)

        ok, _ = self.app_services.reset_user_password(
            token, {"password": "NovaSenha1", "confirm_password": "NovaSenha1"}
        )
        self.assertTrue(ok)
        self.assertIsNotNone(self.app_services.authenticate_user("dp@example.com", "NovaSenha1"))
        self.assertIsNone(self.app_services.authenticate_user("dp@example.com", "Senha123"))
        self.assertIsNone(self.app_services.get_password_reset(token))

        status, payload, _, _ = self.request_http("GET", "/dp/login")
        self.assertEqual(status, 200)
        self.assertIn("Redefinir senha", payload)
        self.assertIn('formaction="/esqueci-senha"', payload)

    def _gama_dp_user(self, name: str = "Equipe DP", email: str = "dp@example.com"):
        _, errors = self.app_services.create_user(
            {"name": name, "email": email, "department": "DP", "role": "dp", "password": "Senha123"},
            initial_setup=True,
        )
        self.assertFalse(errors)
        return self.app_services.authenticate_user(email, "Senha123")

    def test_gama_perfil_empresas_e_copia_fixa_do_dp(self) -> None:
        svc = self.app_services
        gama_id = next(c["id"] for c in svc.list_categories() if c["name"] == "Gama")

        dp = self._gama_dp_user()
        _, errors = svc.create_user(
            {"name": "Lucas", "email": "hugo.reis@gama.example.com", "role": "gama", "password": "Senha123"},
            current_user_id=dp["id"],
        )
        self.assertFalse(errors)
        lucas = svc.authenticate_user("hugo.reis@gama.example.com", "Senha123")
        self.assertEqual(svc.user_allowed_category_names("gama"), ("Gama",))
        self.assertEqual(svc.visible_companies_for_user(lucas), ("Matriz", "Filial 1 ES", "Filial 2 SC", "Filial 4 SP"))
        self.assertEqual(svc.user_role_label("gama"), "Gama")
        # Beta/Alfa nao enxergam a Gama e vice-versa
        self.assertNotIn("Gama", svc.user_allowed_category_names("ambos"))
        self.assertNotIn("Beta", svc.user_allowed_category_names("gama"))

        _, errors, ticket_id = svc.create_ticket(
            {
                "requester_name": "Lucas",
                "category_id": str(gama_id),
                "company_name": "Filial 2 SC",
                "request_category": "Folha mensal",
                "title": "Fechamento da filial",
                "description": "Segue o fechamento da folha da filial de SC.",
            }
        )
        self.assertFalse(errors)
        self.assertTrue(ticket_id)

        # dp@ sempre em copia, em qualquer origem, sem duplicar
        self.assertEqual(svc.ticket_cc_emails_from_form("Gama", {}), ["dp@example.com"])
        self.assertEqual(
            svc.ticket_cc_emails_from_form("Gama", {"cc_gama_hugo_reis": "1"}),
            ["dp@example.com", "hugo.reis@gama.example.com"],
        )
        for origem in ("Alfa", "Beta"):
            emails = svc.ticket_cc_emails_from_form(origem, {})
            self.assertEqual(emails, ["dp@example.com"])
            self.assertNotIn("dp@example.com", [e for _, e, _ in svc.ticket_cc_options(origem)])
        # pessoas da Gama nao aparecem como opcao de copia nas outras empresas
        self.assertNotIn("hugo.reis@gama.example.com", [e for _, e, _ in svc.ticket_cc_options("Alfa")])

    def test_gestao_de_empresas_e_filtro_responsavel_folha(self) -> None:
        svc = self.app_services
        dp = self._gama_dp_user()
        _, errors = svc.create_user(
            {"name": "Caroline", "email": "caroline@example.com", "department": "DP", "role": "dp", "password": "Senha123"},
            current_user_id=dp["id"],
        )
        self.assertFalse(errors)
        caroline = svc.authenticate_user("caroline@example.com", "Senha123")

        def abrir(category_id: str, company: str, title: str) -> int:
            _, errs, tid = svc.create_ticket(
                {
                    "requester_name": "Cliente",
                    "category_id": category_id,
                    "company_name": company,
                    "request_category": "Folha mensal",
                    "title": title,
                    "description": "Descrição suficiente para a solicitação de teste.",
                }
            )
            self.assertFalse(errs)
            return tid

        alfa_ticket = abrir("1", "Alfa Matriz", "Alfa folha")
        beta_ticket = abrir("2", "Beta", "Beta folha")

        def ids(filters):
            tickets, _, _ = svc.get_dashboard_data("todos", svc.get_ticket_filters(filters))
            return {t["id"] for t in tickets}

        # sem atribuicao, o filtro nao devolve nada
        self.assertEqual(ids({"folha_owner": str(caroline["id"])}), set())
        self.assertEqual(ids({}), {alfa_ticket, beta_ticket})

        self.assertFalse(svc.set_category_owner(1, 99999)[0])  # id invalido e recusado
        self.assertEqual(svc.list_category_owners(), {})
        ok, _ = svc.set_category_owner(1, caroline["id"])
        self.assertTrue(ok)
        self.assertEqual(svc.list_category_owners(), {1: {caroline["id"]}})
        self.assertEqual(ids({"folha_owner": str(caroline["id"])}), {alfa_ticket})
        self.assertEqual(ids({"folha_owner": str(dp["id"])}), set())
        # combina com os outros filtros
        self.assertEqual(ids({"folha_owner": str(caroline["id"]), "category_id": "2"}), set())

        # substituir remove o anterior
        svc.set_category_owner(1, dp["id"])  # um so responsavel: troca o anterior
        self.assertEqual(svc.list_category_owners(), {1: {dp["id"]}})
        self.assertEqual(ids({"folha_owner": str(caroline["id"])}), set())
        self.assertEqual(ids({"folha_owner": str(dp["id"])}), {alfa_ticket})
        self.assertFalse(svc.set_category_owner(9999, dp["id"])[0])
        svc.set_category_owner(1, None)  # remover responsavel
        self.assertEqual(svc.list_category_owners(), {})
        svc.set_category_owner(1, dp["id"])

        # telas
        cookie = {"Cookie": f"helpdesk_session={svc.create_session(dp['id'])}"}
        status, payload, _, _ = self.request_http("GET", "/dp", headers=cookie)
        self.assertEqual(status, 200)
        self.assertIn("Responsável folha", payload)
        self.assertIn("Gestão de empresas", payload)
        for removido in ("Solicitações Gama", "Solicitações Beta", "Solicitações Alfa"):
            self.assertNotIn(removido, payload)
        status, payload, _, _ = self.request_http("GET", f"/dp?tab=todos&folha_owner={dp['id']}", headers=cookie)
        self.assertIn("Alfa folha", payload)
        self.assertNotIn("Beta folha", payload)
        status, payload, _, _ = self.request_http("GET", "/dp/empresas", headers=cookie)
        self.assertEqual(status, 200)
        for nome in ("Alfa", "Beta", "Gama", "Matriz", "Caroline"):
            self.assertIn(nome, payload)

        # cliente (nao DP) nao entra na gestao
        _, _ = svc.create_user(
            {"name": "Cli", "email": "cli@example.com", "role": "beta", "password": "Senha123"}, current_user_id=dp["id"]
        )
        cli = svc.authenticate_user("cli@example.com", "Senha123")
        status, _, headers, _ = self.request_http(
            "GET", "/dp/empresas", headers={"Cookie": f"helpdesk_session={svc.create_session(cli['id'])}"}
        )
        self.assertEqual(status, 302)

    def test_confirmacao_de_abertura_leva_dp_em_copia(self) -> None:
        svc = self.app_services
        dp = self._gama_dp_user()
        _, errors, ticket_id = svc.create_ticket(
            {
                "requester_name": "Cliente",
                "requester_email": "cliente@example.com",
                "category_id": "1",
                "company_name": "Alfa Matriz",
                "request_category": "Folha mensal",
                "title": "Confirmacao",
                "description": "Descrição suficiente para a solicitação de teste.",
            },
            current_user=dp,
        )
        self.assertFalse(errors)
        with mock.patch.object(svc, "deliver_email", return_value="outbox") as deliver:
            svc.send_ticket_confirmation(ticket_id)
        self.assertTrue(deliver.called)
        self.assertEqual(deliver.call_args.args[0]["Cc"], "dp@example.com")

    # ------------------------------------------------------------------
    # Tarefa no SharePoint ao concluir (origens com configuracao: Gama)
    # ------------------------------------------------------------------
    def _sp_setup(self):
        svc = self.app_services
        dp = self._gama_dp_user()
        _, errors = svc.create_user(
            {"name": "Lucas", "email": "hugo.reis@gama.example.com", "role": "gama", "password": "Senha123"},
            current_user_id=dp["id"],
        )
        self.assertFalse(errors)
        lucas = svc.authenticate_user("hugo.reis@gama.example.com", "Senha123")
        _, _ = svc.create_user(
            {"name": "Outro", "email": "outro@beta.com", "role": "beta", "password": "Senha123"},
            current_user_id=dp["id"],
        )
        outro = svc.authenticate_user("outro@beta.com", "Senha123")
        cats = {c["name"]: str(c["id"]) for c in svc.list_categories()}
        return svc, dp, lucas, outro, cats

    def _sp_ticket(self, svc, cats, origem, user, company, categoria="Outros", title="Documentos do mês", desc="Segue o pedido."):
        _, errors, ticket_id = svc.create_ticket(
            {
                "requester_name": user["name"],
                "category_id": cats[origem],
                "company_name": company,
                "request_category": categoria,
                "title": title,
                "description": desc,
            },
            current_user=user,
        )
        self.assertFalse(errors)
        return ticket_id

    @staticmethod
    def _sp_file(name="folha.pdf", content=b"%PDF-1.4 conteudo"):
        return {"filename": name, "content": content, "size_bytes": len(content), "content_type": "application/pdf"}

    def test_concluir_com_anexo_na_gama_oculta_anexo_e_agenda_tarefa(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        ticket_id = self._sp_ticket(svc, cats, "Gama", lucas, "Filial 2 SC", categoria="Processos trabalhistas",
                                    title="Acordo trabalhista", desc="<p>Precisamos <b>calcular</b> o acordo.</p>")
        ok, msg = svc.update_ticket(ticket_id, {"status": "Concluido", "comment": ""}, dp["id"], [self._sp_file()])
        self.assertTrue(ok)
        self.assertIn("agendado", msg)

        atts = svc.list_ticket_attachments(ticket_id)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["client_visible"], 0)
        self.assertEqual(atts[0]["uploaded_by_user_id"], dp["id"])
        self.assertEqual(svc.list_ticket_attachments(ticket_id, include_hidden=False), [])

        # nomes dos arquivos nao aparecem para o cliente
        eventos = " ".join(e["message"] for e in svc.get_public_ticket_events(ticket_id))
        self.assertNotIn("folha.pdf", eventos)
        self.assertIn("Equipe DP enviou documentos.", eventos)

        pedido = svc.get_sharepoint_request_for_ticket(ticket_id)
        self.assertEqual(pedido["status"], "pendente")
        import json as _json
        payload = _json.loads(pedido["payload"])
        self.assertEqual(payload["process_id"], 28)  # Processos trabalhistas -> Atividade Eventual
        self.assertEqual(payload["cliente_id"], 273)
        self.assertEqual(payload["area_id"], 3)
        self.assertEqual(payload["revisao_assignee"], "31")
        self.assertFalse(payload["check_existing"])
        from datetime import timedelta as _td
        agora = svc.utc_now().astimezone(svc.DISPLAY_TIMEZONE)
        self.assertEqual(payload["competencia"], f"{svc.MONTH_NAMES_PT[agora.month]} {agora.year}")
        self.assertEqual(payload["prazo"], (agora.date() + _td(days=3)).isoformat())
        self.assertEqual(_json.loads(pedido["attachment_ids"]), [atts[0]["id"]])
        d = payload["descricao"]
        self.assertTrue(d.startswith(f"PortalDP #{ticket_id} — Processos trabalhistas\nFilial 2 SC\nSolicitante: Lucas\nTítulo: Acordo trabalhista\n\n"))
        self.assertIn("Precisamos calcular o acordo.", d)
        self.assertNotIn("<b>", d)
        self.assertNotIn(svc.get_public_ticket_token_by_id(ticket_id), d)  # sem token/link

    def test_mapeamento_categoria_processo_e_processo_mensal(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        import json as _json
        esperado = {"Folha mensal": 9, "Outros": 28, "Processos trabalhistas": 28}
        for categoria, processo in esperado.items():
            tid = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz", categoria=categoria, title=f"T {categoria}")
            svc.update_ticket(tid, {"status": "Concluido"}, dp["id"], [self._sp_file(f"{categoria}.pdf".replace(" ", "_"))])
            payload = _json.loads(svc.get_sharepoint_request_for_ticket(tid)["payload"])
            self.assertEqual(payload["process_id"], processo, categoria)
            self.assertEqual(payload["check_existing"], processo == 9, categoria)

    def test_concluir_sem_anexo_ou_cancelar_nao_gera_tarefa(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        t1 = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz")
        svc.update_ticket(t1, {"status": "Concluido", "comment": "Feito, sem documentos."}, dp["id"], [])
        self.assertIsNone(svc.get_sharepoint_request_for_ticket(t1))

        t2 = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz", title="Outra")
        svc.update_ticket(t2, {"status": "Cancelado"}, dp["id"], [self._sp_file()])
        self.assertIsNone(svc.get_sharepoint_request_for_ticket(t2))
        # anexo enviado em cancelamento ou fora da conclusao segue visivel ao cliente
        self.assertEqual(len(svc.list_ticket_attachments(t2, include_hidden=False)), 1)

        t3 = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz", title="Comentario com anexo")
        svc.update_ticket(t3, {"status": "Em atendimento", "comment": "Segue rascunho"}, dp["id"], [self._sp_file("rascunho.pdf")])
        self.assertIsNone(svc.get_sharepoint_request_for_ticket(t3))
        visiveis = svc.list_ticket_attachments(t3, include_hidden=False)
        self.assertEqual([a["original_name"] for a in visiveis], ["rascunho.pdf"])
        eventos = " ".join(e["message"] for e in svc.get_public_ticket_events(t3))
        self.assertIn("rascunho.pdf", eventos)

    def test_beta_e_alfa_nao_mudam_ao_concluir_com_anexo(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        for origem, company in (("Beta", "Beta"), ("Alfa", "Alfa Matriz")):
            tid = self._sp_ticket(svc, cats, origem, outro if origem == "Beta" else dp, company, title=f"{origem} concluir")
            ok, msg = svc.update_ticket(tid, {"status": "Concluido"}, dp["id"], [self._sp_file(f"{origem}.pdf")])
            self.assertTrue(ok)
            self.assertEqual(msg, "Chamado atualizado.")
            self.assertIsNone(svc.get_sharepoint_request_for_ticket(tid))
            atts = svc.list_ticket_attachments(tid, include_hidden=False)
            self.assertEqual([a["original_name"] for a in atts], [f"{origem}.pdf"])
            eventos = " ".join(e["message"] for e in svc.get_public_ticket_events(tid))
            self.assertIn(f"{origem}.pdf", eventos)
            # download: logado so baixa da propria origem (outro = Beta; lucas = Gama); com o link (token) qualquer um
            self.assertEqual(svc.can_download_attachment(atts[0], outro, ""), origem == "Beta")
            self.assertFalse(svc.can_download_attachment(atts[0], lucas, ""))
            self.assertTrue(svc.can_download_attachment(atts[0], None, svc.get_public_ticket_token_by_id(tid)))

    def test_regras_de_download_das_origens_com_tarefa(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        tid = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz")
        svc.update_ticket(tid, {"status": "Em atendimento", "comment": "rascunho"}, dp["id"], [self._sp_file("visivel.pdf")])
        svc.update_ticket(tid, {"status": "Concluido"}, dp["id"], [self._sp_file("oculto.pdf")])
        token = svc.get_public_ticket_token_by_id(tid)
        por_nome = {a["original_name"]: a for a in svc.list_ticket_attachments(tid)}
        oculto, visivel = por_nome["oculto.pdf"], por_nome["visivel.pdf"]
        # oculto: so agente DP (nem token, nem o proprio solicitante)
        self.assertTrue(svc.can_download_attachment(oculto, dp, ""))
        self.assertFalse(svc.can_download_attachment(oculto, lucas, token))
        self.assertFalse(svc.can_download_attachment(oculto, None, token))
        self.assertFalse(svc.can_download_attachment(oculto, outro, ""))
        # visivel (comentario): DP, solicitante e token; outro usuario logado (ex.: Beta) nao
        self.assertTrue(svc.can_download_attachment(visivel, dp, ""))
        self.assertTrue(svc.can_download_attachment(visivel, lucas, ""))
        self.assertTrue(svc.can_download_attachment(visivel, None, token))
        self.assertFalse(svc.can_download_attachment(visivel, outro, ""))

    def test_telas_e_download_http_da_gama(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        tid = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz", title="Doc do mês")
        svc.update_ticket(tid, {"status": "Concluido"}, dp["id"], [self._sp_file("secreto.pdf", b"conteudo secreto")])
        token = svc.get_public_ticket_token_by_id(tid)
        oculto = svc.list_ticket_attachments(tid)[0]

        status, payload, _, _ = self.request_http("GET", f"/acompanhar/{token}")
        self.assertEqual(status, 200)
        self.assertNotIn("secreto.pdf", payload)
        status, _, _, _ = self.request_http("GET", f"/anexos/{oculto['id']}?token={token}")
        self.assertEqual(status, 404)
        cookie_lucas = {"Cookie": f"helpdesk_session={svc.create_session(lucas['id'])}"}
        status, _, _, _ = self.request_http("GET", f"/anexos/{oculto['id']}", headers=cookie_lucas)
        self.assertEqual(status, 404)

        cookie_dp = {"Cookie": f"helpdesk_session={svc.create_session(dp['id'])}"}
        status, body, _, _ = self.request_http("GET", f"/anexos/{oculto['id']}", headers=cookie_dp)
        self.assertEqual(status, 200)
        self.assertEqual(body, "conteudo secreto")
        status, panel, _, _ = self.request_http("GET", "/dp?tab=todos", headers=cookie_dp)
        self.assertIn("secreto.pdf", panel)
        self.assertIn("oculto ao cliente", panel)
        self.assertIn("Portal de tarefas:", panel)
        self.assertIn("Aguardando envio", panel)

    def test_api_interna_do_worker_sharepoint(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        tid = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz")
        svc.update_ticket(tid, {"status": "Concluido"}, dp["id"], [self._sp_file("folha.pdf", b"BYTES-DA-FOLHA")])
        pedido = svc.get_sharepoint_request_for_ticket(tid)
        auth = {"Authorization": "Bearer segredo-de-teste"}
        with mock.patch.dict(os.environ, {"PORTALDP_INTERNAL_TOKEN": "segredo-de-teste"}):
            # sem token / token errado / vindo pela Cloudflare -> 404
            self.assertEqual(self.request_http("GET", "/interno/sharepoint/pendentes")[0], 404)
            self.assertEqual(self.request_http("GET", "/interno/sharepoint/pendentes", headers={"Authorization": "Bearer errado"})[0], 404)
            self.assertEqual(self.request_http("GET", "/interno/sharepoint/pendentes", headers={**auth, "Cf-Ray": "abc"})[0], 404)

            status, body, _, _ = self.request_http("GET", "/interno/sharepoint/pendentes", headers=auth)
            self.assertEqual(status, 200)
            lista = json.loads(body)["requests"]
            self.assertEqual([r["id"] for r in lista], [pedido["id"]])
            self.assertEqual(lista[0]["attachments"][0]["name"], "folha.pdf")
            self.assertEqual(lista[0]["payload"]["cliente_id"], 273)

            rid = pedido["id"]
            aid = lista[0]["attachments"][0]["id"]
            status, _, _, _ = self.request_http("GET", f"/interno/sharepoint/{rid}/anexos/{aid}", headers=auth)
            self.assertEqual(status, 200)
            status, corpo, _, _ = self.request_http("GET", f"/interno/sharepoint/{rid}/anexos/999999", headers=auth)
            self.assertEqual(status, 404)

            self.assertEqual(self.request_http("POST", f"/interno/sharepoint/{rid}/iniciar", headers=auth)[0], 200)
            self.assertEqual(self.request_http("POST", f"/interno/sharepoint/{rid}/iniciar", headers=auth)[0], 409)  # ja em envio
            self.assertEqual(json.loads(self.request_http("GET", "/interno/sharepoint/pendentes", headers=auth)[1])["requests"], [])

            hdr = {**auth, "Content-Type": "application/json"}
            status, _, _, _ = self.request_http("POST", f"/interno/sharepoint/{rid}/resultado",
                                                body=json.dumps({"status": "enviando", "task_id": 84600, "message": "criada"}), headers=hdr)
            self.assertEqual(status, 200)
            status, _, _, _ = self.request_http("POST", f"/interno/sharepoint/{rid}/resultado",
                                                body=json.dumps({"status": "invalido"}), headers=hdr)
            self.assertEqual(status, 400)
            status, _, _, _ = self.request_http("POST", f"/interno/sharepoint/{rid}/resultado",
                                                body=json.dumps({"status": "criada", "message": "Tarefa criada e enviada para Revisão"}), headers=hdr)
            self.assertEqual(status, 200)
        final = svc.get_sharepoint_request_for_ticket(tid)
        self.assertEqual((final["status"], final["task_id"]), ("criada", 84600))  # task_id mantido

        # sem token configurado no servidor a API nem existe
        with mock.patch.dict(os.environ, {"PORTALDP_INTERNAL_TOKEN": ""}):
            self.assertEqual(self.request_http("GET", "/interno/sharepoint/pendentes", headers=auth)[0], 404)

    def test_reenviar_so_quando_erro_ou_aviso_e_pedido_preso_volta_a_fila(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        tid = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz")
        svc.update_ticket(tid, {"status": "Concluido"}, dp["id"], [self._sp_file()])
        rid = svc.get_sharepoint_request_for_ticket(tid)["id"]
        self.assertFalse(svc.requeue_sharepoint_request(tid)[0])  # pendente: nada a reenviar
        self.assertIsNotNone(svc.claim_sharepoint_request(rid))
        self.assertIsNone(svc.claim_sharepoint_request(rid))
        svc.finish_sharepoint_request(rid, "erro", None, "SharePoint fora do ar")
        self.assertTrue(svc.requeue_sharepoint_request(tid)[0])
        self.assertEqual(svc.get_sharepoint_request_for_ticket(tid)["status"], "pendente")
        svc.claim_sharepoint_request(rid)
        svc.finish_sharepoint_request(rid, "criada", 1, "ok")
        self.assertFalse(svc.requeue_sharepoint_request(tid)[0])
        # 'enviando' parado ha mais de 15 min volta a ser elegivel
        with self.app_db.get_db() as conn:
            conn.execute("UPDATE sharepoint_task_requests SET status='enviando', updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
        self.assertEqual([r["id"] for r in svc.list_pending_sharepoint_requests()], [rid])
        self.assertIsNotNone(svc.claim_sharepoint_request(rid))

    def test_descricao_da_tarefa_e_cortada_mantendo_numero_do_chamado(self) -> None:
        svc, dp, lucas, outro, cats = self._sp_setup()
        tid = self._sp_ticket(svc, cats, "Gama", lucas, "Matriz", desc="palavra " * 490)
        import json as _json
        svc.update_ticket(tid, {"status": "Concluido"}, dp["id"], [self._sp_file()])
        descricao = _json.loads(svc.get_sharepoint_request_for_ticket(tid)["payload"])["descricao"]
        self.assertLessEqual(len(descricao), 3000)
        self.assertTrue(descricao.endswith(f"Texto completo no PortalDP #{tid}."))


    # ---- Enviar chave Pix na resposta do DP (Beta/Alfa; Folha mensal, Ferias, Rescisao) ----

    def _pix_setup(self):
        svc = self.app_services
        dp = self._gama_dp_user()
        cats = {c["name"]: c["id"] for c in svc.list_categories()}
        return svc, dp, cats

    def _pix_add(self, origem: str, nome: str, chave: str, tipo: str = "") -> int:
        svc = self.app_services
        cat = next(c["id"] for c in svc.list_categories() if c["name"] == origem)
        with self.app_db.get_db() as conn:
            cur = conn.execute(
                "INSERT INTO pix_keys (category_id, employee_name, name_key, pix_key, key_type) VALUES (?, ?, ?, ?, ?)",
                (cat, nome, svc.pix_name_key(nome), chave, tipo),
            )
            return cur.lastrowid

    def _pix_ticket(self, cats, origem="Beta", categoria="Folha mensal", company="Beta", extra=None, user=None) -> int:
        form = {
            "requester_name": "Solicitante",
            "category_id": str(cats[origem]),
            "company_name": company,
            "request_category": categoria,
            "title": f"{categoria} teste",
            "description": "Texto da solicitação com tamanho suficiente.",
        }
        if categoria == "Rescisão":
            form["rescission_type"] = "Pedido de demissão"
            form["rescission_date"] = "2026-10-30"
        form.update(extra or {})
        _, errors, ticket_id = self.app_services.create_ticket(form, None, user)
        self.assertFalse(errors, errors)
        return ticket_id

    def _pix_events(self, ticket_id: int):
        with self.app_db.get_db() as conn:
            return conn.execute(
                "SELECT message, event_type FROM ticket_events WHERE ticket_id = ? AND event_type = 'comentario' ORDER BY id",
                (ticket_id,),
            ).fetchall()

    def _pix_row(self, nome: str, origem: str = "Beta"):
        svc = self.app_services
        cat = next(c["id"] for c in svc.list_categories() if c["name"] == origem)
        with self.app_db.get_db() as conn:
            return conn.execute(
                "SELECT * FROM pix_keys WHERE category_id = ? AND name_key = ?", (cat, svc.pix_name_key(nome))
            ).fetchone()

    def test_pix_regras_de_aplicabilidade_e_normalizacao_de_nome(self) -> None:
        svc = self.app_services
        self.assertTrue(svc.pix_enabled_for("Beta", "Folha mensal"))
        self.assertTrue(svc.pix_enabled_for("Alfa", "Férias"))
        self.assertTrue(svc.pix_enabled_for("beta", "Rescisão"))
        self.assertFalse(svc.pix_enabled_for("Gama", "Folha mensal"))
        self.assertFalse(svc.pix_enabled_for("Beta", "Admissão"))
        self.assertFalse(svc.pix_enabled_for("Alfa", "Outros"))
        self.assertEqual(svc.pix_name_key("  joão   da  SILVA "), "JOAO DA SILVA")

    def test_pix_chave_cadastrada_vai_ao_fim_da_mensagem_com_nome_e_chave(self) -> None:
        svc, dp, cats = self._pix_setup()
        pix_id = self._pix_add("Beta", "ALAN MOURA TEIXEIRA", "52998224725", "CPF")
        _, errors = svc.create_user(
            {"name": "Cliente Beta", "email": "cliente@beta.test", "role": "beta", "password": "Senha123"}, current_user_id=dp["id"]
        )
        self.assertFalse(errors)
        cliente = svc.authenticate_user("cliente@beta.test", "Senha123")
        ticket_id = self._pix_ticket(cats, user=cliente)
        ok, msg = svc.update_ticket(
            ticket_id,
            {"status": "Em atendimento", "comment": "Segue o retorno da folha.", "pix_send": "1",
             "pix_id_0": str(pix_id), "pix_name_0": "ALAN MOURA TEIXEIRA", "pix_key_0": "52998224725", "pix_type_0": "CPF"},
            dp["id"],
        )
        self.assertTrue(ok, msg)
        message = self._pix_events(ticket_id)[-1]["message"]
        self.assertTrue(message.startswith("Segue o retorno da folha."))
        self.assertTrue(message.endswith("Chave Pix para pagamento:<br>ALAN MOURA TEIXEIRA — CPF: 52998224725"))
        # chave e tipo nao mudaram: nenhum historico novo
        with self.app_db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM pix_key_history").fetchone()["n"], 0)
        # o e-mail ao solicitante leva a mesma linha
        import email
        from email import policy

        bodies = [
            email.message_from_bytes(f.read_bytes(), policy=policy.default).get_body().get_content()
            for f in Path(os.environ["APP_OUTBOX_PATH"]).glob("*ticket-update-*.eml")
        ]
        self.assertTrue(any("Chave Pix para pagamento:\nALAN MOURA TEIXEIRA — CPF: 52998224725" in b for b in bodies), bodies)

    def test_pix_sem_texto_sai_so_com_a_linha_da_chave_e_varios_funcionarios(self) -> None:
        svc, dp, cats = self._pix_setup()
        a = self._pix_add("Beta", "ANA CLARA", "111")
        b = self._pix_add("Beta", "BRUNO LIMA", "bruno@x.com")
        ticket_id = self._pix_ticket(cats)
        ok, msg = svc.update_ticket(
            ticket_id,
            {"status": "Em atendimento", "pix_send": "1",
             "pix_id_0": str(a), "pix_key_0": "111", "pix_type_0": "CPF",
             "pix_id_3": str(b), "pix_key_3": "bruno@x.com", "pix_type_3": "E-mail"},
            dp["id"],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(
            self._pix_events(ticket_id)[-1]["message"],
            "Chave Pix para pagamento:<br>ANA CLARA — CPF: 111<br>BRUNO LIMA — E-mail: bruno@x.com",
        )

    def test_pix_funcionario_sem_chave_exige_digitar_e_so_salva_com_a_resposta(self) -> None:
        svc, dp, cats = self._pix_setup()
        pix_id = self._pix_add("Beta", "DAVID RIBEIRO SILVA", "")
        ticket_id = self._pix_ticket(cats)
        base = {"status": "Em atendimento", "comment": "Retorno.", "pix_send": "1", "pix_id_0": str(pix_id), "pix_type_0": "Telefone"}
        ok, msg = svc.update_ticket(ticket_id, {**base, "pix_key_0": "  "}, dp["id"])
        self.assertFalse(ok)
        self.assertIn("DAVID RIBEIRO SILVA", msg)
        self.assertEqual(self._pix_row("DAVID RIBEIRO SILVA")["pix_key"], "")
        self.assertEqual(self._pix_events(ticket_id), [])  # nada foi enviado
        with self.app_db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()["status"], "Aberto")

        ok, msg = svc.update_ticket(ticket_id, {**base, "pix_key_0": "61999998888"}, dp["id"])
        self.assertTrue(ok, msg)
        self.assertEqual(self._pix_row("DAVID RIBEIRO SILVA")["pix_key"], "61999998888")
        with self.app_db.get_db() as conn:
            hist = conn.execute("SELECT old_key, new_key, changed_by_user_id, ticket_id FROM pix_key_history").fetchall()
        self.assertEqual([(h["old_key"], h["new_key"], h["changed_by_user_id"], h["ticket_id"]) for h in hist],
                         [("", "61999998888", dp["id"], ticket_id)])

    def test_pix_corrigir_chave_existente_atualiza_e_guarda_historico(self) -> None:
        svc, dp, cats = self._pix_setup()
        pix_id = self._pix_add("Beta", "ANA CLARA XIMENES", "6712345678")
        ticket_id = self._pix_ticket(cats)
        ok, msg = svc.update_ticket(
            ticket_id,
            {"status": "Em atendimento", "pix_send": "1", "pix_id_0": str(pix_id), "pix_key_0": "06712345678", "pix_type_0": "CPF"},
            dp["id"],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(self._pix_row("ANA CLARA XIMENES")["pix_key"], "06712345678")
        with self.app_db.get_db() as conn:
            hist = conn.execute("SELECT old_key, new_key FROM pix_key_history").fetchall()
        self.assertEqual([(h["old_key"], h["new_key"]) for h in hist], [("6712345678", "06712345678")])
        self.assertTrue(self._pix_events(ticket_id)[-1]["message"].endswith("ANA CLARA XIMENES — CPF: 06712345678"))

    def test_pix_cadastra_funcionario_novo_na_origem_do_chamado_sem_duplicar(self) -> None:
        svc, dp, cats = self._pix_setup()
        ticket_id = self._pix_ticket(cats, origem="Alfa", company="Alfa Matriz")
        ok, msg = svc.update_ticket(
            ticket_id,
            {"status": "Em atendimento", "pix_send": "1", "pix_id_0": "0",
             "pix_name_0": "joão   da silva", "pix_key_0": "joao@x.com", "pix_type_0": "E-mail"},
            dp["id"],
        )
        self.assertTrue(ok, msg)
        row = self._pix_row("JOAO DA SILVA", "Alfa")
        self.assertEqual((row["employee_name"], row["pix_key"], row["key_type"]), ("JOÃO DA SILVA", "joao@x.com", "E-mail"))
        self.assertIsNone(self._pix_row("JOAO DA SILVA", "Beta"))  # nao vaza para a outra origem
        # mesmo nome de novo (outra grafia): reaproveita o cadastro, nao duplica
        t2 = self._pix_ticket(cats, origem="Alfa", company="Alfa Matriz")
        ok, msg = svc.update_ticket(
            t2,
            {"status": "Em atendimento", "pix_send": "1", "pix_id_0": "0",
             "pix_name_0": "JOAO DA SILVA", "pix_key_0": "joao@x.com", "pix_type_0": "E-mail"},
            dp["id"],
        )
        self.assertTrue(ok, msg)
        with self.app_db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM pix_keys").fetchone()["n"], 1)

    def test_pix_nao_aplica_a_gama_nem_a_outras_categorias_e_nao_grava_nada(self) -> None:
        svc, dp, cats = self._pix_setup()
        for origem, categoria, company in (("Gama", "Folha mensal", "Matriz"), ("Beta", "Outros", "Beta")):
            ticket_id = self._pix_ticket(cats, origem=origem, categoria=categoria, company=company)
            ok, msg = svc.update_ticket(
                ticket_id,
                {"status": "Em atendimento", "pix_send": "1", "pix_id_0": "0", "pix_name_0": "FULANO", "pix_key_0": "123", "pix_type_0": "CPF"},
                dp["id"],
            )
            self.assertFalse(ok)
            self.assertIn("não se aplica", msg)
        with self.app_db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM pix_keys").fetchone()["n"], 0)

    def test_pix_funcionario_de_outra_origem_e_recusado(self) -> None:
        svc, dp, cats = self._pix_setup()
        beta_id = self._pix_add("Beta", "ANA CLARA", "111")
        ticket_id = self._pix_ticket(cats, origem="Alfa", company="Alfa Matriz")
        ok, msg = svc.update_ticket(
            ticket_id, {"status": "Em atendimento", "pix_send": "1", "pix_id_0": str(beta_id), "pix_key_0": "111", "pix_type_0": "CPF"}, dp["id"]
        )
        self.assertFalse(ok)
        self.assertIn("origem", msg)

    def test_pix_sem_funcionario_selecionado_e_recusado_e_sem_marcar_nada_muda(self) -> None:
        svc, dp, cats = self._pix_setup()
        ticket_id = self._pix_ticket(cats)
        ok, msg = svc.update_ticket(ticket_id, {"status": "Em atendimento", "comment": "x", "pix_send": "1"}, dp["id"])
        self.assertFalse(ok)
        self.assertIn("pelo menos um funcionário", msg)
        # sem o checkbox o comportamento de sempre
        ok, msg = svc.update_ticket(ticket_id, {"status": "Em atendimento", "comment": "Resposta comum."}, dp["id"])
        self.assertTrue(ok, msg)
        self.assertEqual(self._pix_events(ticket_id)[-1]["message"], "Resposta comum.")

    def test_pix_em_chamado_concluido_reabre_como_qualquer_resposta(self) -> None:
        svc, dp, cats = self._pix_setup()
        pix_id = self._pix_add("Beta", "ANA CLARA", "111")
        ticket_id = self._pix_ticket(cats, categoria="Rescisão")
        self.assertTrue(svc.update_ticket(ticket_id, {"status": "Concluido", "comment": "Pronto."}, dp["id"])[0])
        ok, msg = svc.update_ticket(
            ticket_id, {"status": "Concluido", "pix_send": "1", "pix_id_0": str(pix_id), "pix_key_0": "111", "pix_type_0": "CPF"}, dp["id"]
        )
        self.assertTrue(ok, msg)
        with self.app_db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()["status"], "Em atendimento")

    def test_pix_ferias_sugere_o_funcionario_do_chamado_inclusive_nome_cortado(self) -> None:
        svc, dp, cats = self._pix_setup()
        completo = "FRANCISCO AUGUSTO VARGAS NUNES"
        self._pix_add("Beta", completo[:32], "111")  # a planilha original cortou em 32 caracteres
        ticket_id = self._pix_ticket(
            cats, categoria="Férias",
            extra={"vacation_subcategory": "Recibo de férias", "vacation_employee_name": completo,
                   "vacation_start_date": "2026-11-03", "vacation_days": "30", "vacation_has_bonus": "Não"},
        )
        with self.app_db.get_db() as conn:
            ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        suggestion = svc.pix_suggestion_for_ticket(ticket)
        self.assertEqual((suggestion["name"], suggestion["key"], suggestion["type"]), (completo[:32], "111", ""))
        # sem cadastro: sugere um novo com o nome do chamado
        t2 = self._pix_ticket(
            cats, categoria="Férias",
            extra={"vacation_subcategory": "Recibo de férias", "vacation_employee_name": "Maria da Silva",
                   "vacation_start_date": "2026-11-03", "vacation_days": "30", "vacation_has_bonus": "Não"},
        )
        with self.app_db.get_db() as conn:
            ticket2 = conn.execute("SELECT * FROM tickets WHERE id = ?", (t2,)).fetchone()
        self.assertEqual(svc.pix_suggestion_for_ticket(ticket2), {"id": 0, "name": "MARIA DA SILVA", "key": "", "type": ""})

    def test_pix_detecta_tipo_da_chave_so_quando_tem_certeza(self) -> None:
        detect = self.app_services.detect_pix_key_type
        casos = {
            "52998224725": "CPF", "529.982.247-25": "CPF",
            "11.222.333/0001-81": "CNPJ", "11222333000181": "CNPJ",
            "61912345678": "Telefone", "61 91234-5678": "Telefone", "+55 61 91234-5678": "Telefone",
            "fulano@gmail.com": "E-mail", "123e4567-e89b-42d3-a456-426614174000": "Chave aleatória",
            # incertos: o agente escolhe
            "6712345678": "", "912345678": "", "61 91234567": "", "": "", "abc": "", "11111111111": "",
        }
        for chave, tipo in casos.items():
            self.assertEqual(detect(chave), tipo, chave)

    def test_pix_tipo_da_chave_e_obrigatorio_e_validado(self) -> None:
        svc, dp, cats = self._pix_setup()
        pix_id = self._pix_add("Beta", "ANA CLARA", "52998224725")
        ticket_id = self._pix_ticket(cats)
        base = {"status": "Em atendimento", "comment": "x", "pix_send": "1", "pix_id_0": str(pix_id), "pix_key_0": "52998224725"}
        ok, msg = svc.update_ticket(ticket_id, base, dp["id"])
        self.assertFalse(ok)
        self.assertIn("tipo da chave Pix de ANA CLARA", msg)
        ok, msg = svc.update_ticket(ticket_id, {**base, "pix_type_0": "PIX"}, dp["id"])
        self.assertFalse(ok)
        self.assertIn("Tipo de chave Pix inválido", msg)
        self.assertEqual(self._pix_events(ticket_id), [])
        self.assertEqual(self._pix_row("ANA CLARA")["key_type"], "")

    def test_pix_tipo_sugerido_na_busca_e_tipo_confirmado_fica_salvo(self) -> None:
        svc, dp, cats = self._pix_setup()
        pix_id = self._pix_add("Beta", "ANA CLARA", "52998224725")  # como na carga da planilha: sem tipo
        self.assertEqual(svc.search_pix_keys(cats["Beta"], "ana")[0]["type"], "CPF")  # sugerido pela deteccao
        ticket_id = self._pix_ticket(cats)
        ok, msg = svc.update_ticket(
            ticket_id,
            {"status": "Em atendimento", "pix_send": "1", "pix_id_0": str(pix_id), "pix_key_0": "52998224725", "pix_type_0": "Telefone"},
            dp["id"],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(self._pix_row("ANA CLARA")["key_type"], "Telefone")  # o agente manda
        self.assertEqual(svc.search_pix_keys(cats["Beta"], "ana")[0]["type"], "Telefone")
        with self.app_db.get_db() as conn:
            hist = conn.execute("SELECT old_key, new_key, old_type, new_type FROM pix_key_history").fetchall()
        self.assertEqual([tuple(h[k] for k in ("old_key", "new_key", "old_type", "new_type")) for h in hist],
                         [("52998224725", "52998224725", "", "Telefone")])
        self.assertTrue(self._pix_events(ticket_id)[-1]["message"].endswith("ANA CLARA — Telefone: 52998224725"))

    def test_pix_busca_http_so_para_dp_e_so_da_origem_pedida(self) -> None:
        svc, dp, cats = self._pix_setup()
        self._pix_add("Beta", "ALAN MOURA", "1")
        self._pix_add("Alfa", "ALAN ALFA", "2")
        _, errors = svc.create_user(
            {"name": "Cliente", "email": "cli@example.com", "role": "beta", "password": "Senha123"}, current_user_id=dp["id"]
        )
        self.assertFalse(errors)
        cli = svc.authenticate_user("cli@example.com", "Senha123")
        dp_cookie = {"Cookie": f"helpdesk_session={svc.create_session(dp['id'])}"}
        cli_cookie = {"Cookie": f"helpdesk_session={svc.create_session(cli['id'])}"}

        status, payload, headers, _ = self.request_http("GET", f"/dp/pix/buscar?category_id={cats['Beta']}&q=alan", headers=dp_cookie)
        self.assertEqual(status, 200)
        self.assertEqual([r["name"] for r in json.loads(payload)["results"]], ["ALAN MOURA"])
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        status, payload, _, _ = self.request_http("GET", f"/dp/pix/buscar?category_id={cats['Alfa']}&q=alan", headers=dp_cookie)
        self.assertEqual([r["name"] for r in json.loads(payload)["results"]], ["ALAN ALFA"])
        status, _, _, _ = self.request_http("GET", f"/dp/pix/buscar?category_id={cats['Gama']}&q=alan", headers=dp_cookie)
        self.assertEqual(status, 404)
        status, payload, _, _ = self.request_http("GET", f"/dp/pix/buscar?category_id={cats['Beta']}&q=alan", headers=cli_cookie)
        self.assertNotEqual(status, 200)
        self.assertNotIn("ALAN", payload)
        status, _, _, _ = self.request_http("GET", f"/dp/pix/buscar?category_id={cats['Beta']}&q=alan")
        self.assertNotEqual(status, 200)

    def test_pix_checkbox_aparece_so_nos_chamados_aplicaveis_do_painel(self) -> None:
        svc, dp, cats = self._pix_setup()
        self._pix_ticket(cats, origem="Beta", categoria="Folha mensal", company="Beta")
        cookie = {"Cookie": f"helpdesk_session={svc.create_session(dp['id'])}"}
        status, payload, _, _ = self.request_http("GET", "/dp?tab=todos", headers=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload.count('name="pix_send"'), 1)
        self.assertIn("Enviar chave pix", payload)
        self.assertIn("/dp/pix/buscar", payload)  # script carregado
        # so chamados nao aplicaveis: nem checkbox nem script
        with self.app_db.get_db() as conn:
            conn.execute("DELETE FROM tickets")
        self._pix_ticket(cats, origem="Beta", categoria="Outros", company="Beta")
        self._pix_ticket(cats, origem="Gama", categoria="Folha mensal", company="Matriz")
        status, payload, _, _ = self.request_http("GET", "/dp?tab=todos", headers=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn('name="pix_send"', payload)
        self.assertNotIn("/dp/pix/buscar", payload)

    def test_pix_importacao_entra_como_esta_e_nao_sobrescreve(self) -> None:
        import importlib.util

        svc = self.app_services
        csv_path = Path(self.temp_dir.name) / "pix.csv"
        csv_path.write_text(
            "Nome,PIX\nANA CLARA XIMENES,6712345678\nDAVID SILVA,\n  BRUNO LIMA  , 61 91234567 \nANA CLARA XIMENES,999\n",
            encoding="utf-8",
        )
        spec = importlib.util.spec_from_file_location("import_pix_under_test", PROJECT_ROOT / "scripts" / "import_pix.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(sys, "argv", ["import_pix.py", str(csv_path), "--origem", "Beta"]):
            self.assertEqual(module.main(), 0)
        self.assertEqual(self._pix_row("ANA CLARA XIMENES")["pix_key"], "6712345678")  # sem zero a esquerda: como veio
        self.assertEqual(self._pix_row("DAVID SILVA")["pix_key"], "")
        self.assertEqual(self._pix_row("BRUNO LIMA")["pix_key"], "61 91234567")
        # correcao feita no portal nao e sobrescrita por nova importacao
        with self.app_db.get_db() as conn:
            conn.execute("UPDATE pix_keys SET pix_key = '06712345678' WHERE name_key = 'ANA CLARA XIMENES'")
        with mock.patch.object(sys, "argv", ["import_pix.py", str(csv_path), "--origem", "Beta"]):
            self.assertEqual(module.main(), 0)
        self.assertEqual(self._pix_row("ANA CLARA XIMENES")["pix_key"], "06712345678")
        with self.app_db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM pix_keys").fetchone()["n"], 3)
        # dry-run nao grava
        with mock.patch.object(sys, "argv", ["import_pix.py", str(csv_path), "--origem", "Alfa", "--dry-run"]):
            self.assertEqual(module.main(), 0)
        with self.app_db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM pix_keys").fetchone()["n"], 3)


    # ---- Cada origem ve so as proprias solicitacoes (lista, busca, contadores, home, download) ----

    def _iso_setup(self):
        svc = self.app_services
        dp = self._gama_dp_user()
        cats = {c["name"]: c["id"] for c in svc.list_categories()}
        users = {}
        for role in ("beta", "alfa", "ambos", "gama"):
            _, errors = svc.create_user(
                {"name": f"User {role}", "email": f"{role}@iso.test", "role": role, "password": "Senha123"},
                current_user_id=dp["id"],
            )
            self.assertFalse(errors, errors)
            users[role] = svc.authenticate_user(f"{role}@iso.test", "Senha123")
        company = {"Beta": "Beta", "Alfa": "Alfa Matriz", "Gama": "Matriz"}
        tickets = {}
        for origem in ("Beta", "Alfa", "Gama"):
            _, errors, tid = svc.create_ticket(
                {"requester_name": "Solicitante Teste", "category_id": str(cats[origem]), "company_name": company[origem],
                 "request_category": "Folha mensal", "title": f"CHAMADO {origem.upper()}",
                 "description": "Texto da solicitação com tamanho suficiente."},
                None, users.get({"Beta": "beta", "Alfa": "alfa", "Gama": "gama"}[origem]),
            )
            self.assertFalse(errors, errors)
            tickets[origem] = tid
            if origem != "Gama":
                svc.update_ticket(tid, {"status": "Em atendimento", "comment": "rascunho"}, dp["id"], [self._sp_file(f"{origem}.pdf")])
        return svc, dp, users, tickets

    def _iso_get(self, svc, user, path):
        cookie = {"Cookie": f"helpdesk_session={svc.create_session(user['id'])}"}
        return self.request_http("GET", path, headers=cookie)

    def test_isolamento_lista_busca_e_contadores_por_origem(self) -> None:
        import re

        svc, dp, users, tickets = self._iso_setup()
        esperado = {
            "beta": {"CHAMADO BETA"},
            "alfa": {"CHAMADO ALFA"},
            "ambos": {"CHAMADO BETA", "CHAMADO ALFA"},
            "gama": {"CHAMADO GAMA"},
        }
        for role, titulos in esperado.items():
            for path in ("/minhas-solicitacoes?q=chamado", "/minhas-solicitacoes?tab=todos"):
                status, payload, _, _ = self._iso_get(svc, users[role], path)
                self.assertEqual(status, 200)
                self.assertEqual(set(re.findall(r"<h3>(.*?)</h3>", payload)), titulos, (role, path))
            # contador da aba "Todos" so conta a(s) propria(s) origem(ns)
            _, payload, _, _ = self._iso_get(svc, users[role], "/minhas-solicitacoes?tab=todos")
            self.assertIn(f"Todos <span>{len(titulos)}</span>", payload, role)
        # pesquisar pelo numero de um chamado de OUTRA origem nao o revela
        status, loc, _ = None, None, None
        for role, outra in (("gama", "Beta"), ("beta", "Gama"), ("alfa", "Beta"), ("ambos", "Gama")):
            status, _, headers, _ = self._iso_get(svc, users[role], f"/buscar-solicitacao?q={tickets[outra]}")
            self.assertEqual(status, 302)
            self.assertTrue(headers["Location"].startswith("/minhas-solicitacoes"), (role, headers["Location"]))
            _, payload, _, _ = self._iso_get(svc, users[role], headers["Location"])
            self.assertNotIn(f"CHAMADO {outra.upper()}", payload, (role, outra))
        # o proprio chamado pelo numero continua indo direto
        status, _, headers, _ = self._iso_get(svc, users["gama"], f"/buscar-solicitacao?q={tickets['Gama']}")
        self.assertTrue(headers["Location"].startswith("/acompanhar/"))
        # Agente DP continua vendo tudo no painel
        _, payload, _, _ = self._iso_get(svc, dp, "/dp?tab=todos")
        for origem in ("BETA", "ALFA", "GAMA"):
            self.assertIn(f"CHAMADO {origem}", payload)

    def test_isolamento_painel_da_home_por_origem(self) -> None:
        svc, dp, users, tickets = self._iso_setup()
        for t in tickets.values():
            svc.update_ticket(t, {"status": "Em atendimento", "comment": "andamento"}, dp["id"])
        for role, titulos in {"ambos": {"CHAMADO BETA", "CHAMADO ALFA"}, "beta": {"CHAMADO BETA"},
                              "alfa": {"CHAMADO ALFA"}, "gama": {"CHAMADO GAMA"}}.items():
            andamento, _ = svc.get_public_dashboard_snapshot(user=users[role])
            self.assertEqual({r["title"] for r in andamento}, titulos, role)

    def test_isolamento_download_de_anexo_por_origem(self) -> None:
        svc, dp, users, tickets = self._iso_setup()
        anexos = {o: svc.list_ticket_attachments(tickets[o])[0]["id"] for o in ("Beta", "Alfa")}
        casos = {  # (usuario, origem do anexo) -> status esperado
            ("beta", "Beta"): 200, ("beta", "Alfa"): 404,
            ("alfa", "Alfa"): 200, ("alfa", "Beta"): 404,
            ("ambos", "Beta"): 200, ("ambos", "Alfa"): 200,
            ("gama", "Beta"): 404, ("gama", "Alfa"): 404,
        }
        for (role, origem), esperado in casos.items():
            status, _, _, _ = self._iso_get(svc, users[role], f"/anexos/{anexos[origem]}")
            self.assertEqual(status, esperado, (role, origem))
        status, _, _, _ = self._iso_get(svc, dp, f"/anexos/{anexos['Beta']}")
        self.assertEqual(status, 200)
        # pelo link publico (token) continua baixando, mesmo sem login e de outra origem
        token = svc.get_public_ticket_token_by_id(tickets["Beta"])
        status, _, _, _ = self.request_http("GET", f"/anexos/{anexos['Beta']}?token={token}")
        self.assertEqual(status, 200)
        status, _, _, _ = self._iso_get(svc, users["gama"], f"/anexos/{anexos['Beta']}?token={token}")
        self.assertEqual(status, 200)

    # ---- Gestao de Pix (/dp/pix) ----

    def _pix_mgmt_session(self):
        svc, dp, cats = self._pix_setup()
        token = svc.create_session(dp["id"])
        return svc, dp, cats, {"Cookie": f"helpdesk_session={token}; helpdesk_csrf=csrf-teste"}

    def _pix_mgmt_post(self, path: str, cookies: dict[str, str], **fields):
        from urllib.parse import urlencode
        body = urlencode({"csrf_token": "csrf-teste", **fields})
        return self.request_http("POST", path, body, {**cookies, "Content-Type": "application/x-www-form-urlencoded"})

    def test_gestao_pix_botao_e_lista_por_origem(self) -> None:
        svc, _dp, cats, cookies = self._pix_mgmt_session()
        self._pix_add("Beta", "ALAN MOURA", "52998224725", "CPF")
        self._pix_add("Alfa", "BRUNA COSTA", "bruna@example.com")
        self.assertIn('href="/dp/pix"', self.request_http("GET", "/dp", headers=cookies)[1])
        status, page, _, _ = self.request_http("GET", "/dp/pix", headers=cookies)
        self.assertEqual(status, 200)
        self.assertIn("ALAN MOURA", page)
        self.assertIn("BRUNA COSTA", page)
        self.assertIn("52998224725", page)
        _, filtrado, _, _ = self.request_http("GET", f"/dp/pix?category_id={cats['Beta']}", headers=cookies)
        self.assertIn("ALAN MOURA", filtrado)
        self.assertNotIn("BRUNA COSTA", filtrado)
        _, busca, _, _ = self.request_http("GET", "/dp/pix?q=bruna", headers=cookies)
        self.assertIn("BRUNA COSTA", busca)
        self.assertNotIn("ALAN MOURA", busca)

    def test_gestao_pix_so_agente_dp(self) -> None:
        svc, _dp, cats, _cookies = self._pix_mgmt_session()
        pix_id = self._pix_add("Beta", "ALAN MOURA", "52998224725", "CPF")
        _, errors = svc.create_user({"name": "Cliente", "email": "cli@example.com", "role": "beta", "password": "Senha123"})
        self.assertFalse(errors)
        cli = svc.authenticate_user("cli@example.com", "Senha123")
        cli_cookies = {"Cookie": f"helpdesk_session={svc.create_session(cli['id'])}; helpdesk_csrf=csrf-teste"}
        status, page, headers, _ = self.request_http("GET", "/dp/pix", headers=cli_cookies)
        self.assertEqual(status, 302)
        self.assertNotIn("52998224725", page)
        status, _, _, _ = self._pix_mgmt_post(f"/dp/pix/{pix_id}/excluir", cli_cookies)
        self.assertEqual(status, 302)
        self.assertIsNotNone(self._pix_row("ALAN MOURA"))
        self.assertEqual(self.request_http("GET", "/dp/pix")[0], 302)

    def test_gestao_pix_adicionar_detecta_tipo_e_grava_historico(self) -> None:
        svc, _dp, cats, cookies = self._pix_mgmt_session()
        status, page, _, _ = self._pix_mgmt_post(
            "/dp/pix/adicionar", cookies, name="joão da silva", key="52998224725", origin_id=str(cats["Alfa"])
        )
        self.assertEqual(status, 200)
        self.assertIn("cadastrado em Alfa", page)
        row = self._pix_row("JOAO DA SILVA", "Alfa")
        self.assertEqual(row["employee_name"], "JOÃO DA SILVA")
        self.assertEqual(row["pix_key"], "52998224725")
        self.assertEqual(row["key_type"], "CPF")
        with self.app_db.get_db() as conn:
            hist = conn.execute("SELECT * FROM pix_key_history WHERE pix_key_id = ?", (row["id"],)).fetchall()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["new_key"], "52998224725")

    def test_gestao_pix_adicionar_recusa_invalidos_e_duplicado(self) -> None:
        svc, _dp, cats, cookies = self._pix_mgmt_session()
        self._pix_add("Beta", "ALAN MOURA", "52998224725", "CPF")
        gama = cats["Gama"]
        for fields, esperado in (
            ({"name": "NOVO", "key": "x@y.com", "origin_id": str(gama)}, "Selecione a origem"),
            ({"name": "", "key": "x@y.com", "origin_id": str(cats["Beta"])}, "Informe o nome"),
            ({"name": "NOVO", "key": "", "origin_id": str(cats["Beta"])}, "Informe a chave"),
            ({"name": "alan  moura", "key": "x@y.com", "origin_id": str(cats["Beta"])}, "já está cadastrado"),
        ):
            _, page, _, _ = self._pix_mgmt_post("/dp/pix/adicionar", cookies, **fields)
            self.assertIn(esperado, page)
        self.assertIsNone(self._pix_row("NOVO"))
        # mesmo nome em outra origem e permitido: as listas sao separadas
        _, page, _, _ = self._pix_mgmt_post("/dp/pix/adicionar", cookies, name="ALAN MOURA", key="x@y.com", origin_id=str(cats["Alfa"]))
        self.assertIn("cadastrado em Alfa", page)
        self.assertEqual(self._pix_row("ALAN MOURA", "Alfa")["key_type"], "E-mail")

    def test_gestao_pix_alterar_nome_chave_tipo_com_historico(self) -> None:
        svc, _dp, cats, cookies = self._pix_mgmt_session()
        pix_id = self._pix_add("Beta", "ALAN MOURA", "52998224725", "CPF")
        self._pix_add("Beta", "BRUNA COSTA", "bruna@example.com", "E-mail")
        _, page, _, _ = self._pix_mgmt_post(
            f"/dp/pix/{pix_id}/atualizar", cookies, name="Alan Moura Teixeira", key="51999998888", key_type="Telefone"
        )
        self.assertIn("atualizado", page)
        row = self._pix_row("ALAN MOURA TEIXEIRA")
        self.assertEqual((row["id"], row["pix_key"], row["key_type"]), (pix_id, "51999998888", "Telefone"))
        with self.app_db.get_db() as conn:
            hist = conn.execute("SELECT * FROM pix_key_history WHERE pix_key_id = ?", (pix_id,)).fetchall()
        self.assertEqual([(h["old_key"], h["new_key"], h["old_type"]) for h in hist], [("52998224725", "51999998888", "CPF")])
        # recusas: nome de outro cadastro, chave apagada, tipo invalido
        _, page, _, _ = self._pix_mgmt_post(f"/dp/pix/{pix_id}/atualizar", cookies, name="BRUNA COSTA", key="1", key_type="")
        self.assertIn("já está cadastrado", page)
        _, page, _, _ = self._pix_mgmt_post(f"/dp/pix/{pix_id}/atualizar", cookies, name="ALAN MOURA TEIXEIRA", key="", key_type="")
        self.assertIn("use Excluir", page)
        _, page, _, _ = self._pix_mgmt_post(f"/dp/pix/{pix_id}/atualizar", cookies, name="ALAN MOURA TEIXEIRA", key="1", key_type="Outro")
        self.assertIn("Tipo de chave Pix inválido", page)
        self.assertEqual(self._pix_row("ALAN MOURA TEIXEIRA")["pix_key"], "51999998888")

    def test_gestao_pix_sem_chave_pode_alterar_nome_e_receber_chave(self) -> None:
        svc, _dp, cats, cookies = self._pix_mgmt_session()
        pix_id = self._pix_add("Beta", "SEM CHAVE", "")
        _, page, _, _ = self._pix_mgmt_post(f"/dp/pix/{pix_id}/atualizar", cookies, name="SEM CHAVE AINDA", key="", key_type="")
        self.assertIn("atualizado", page)
        _, page, _, _ = self._pix_mgmt_post(f"/dp/pix/{pix_id}/atualizar", cookies, name="SEM CHAVE AINDA", key="a@b.com", key_type="")
        self.assertEqual(self._pix_row("SEM CHAVE AINDA")["key_type"], "E-mail")

    def test_gestao_pix_excluir_remove_cadastro_e_historico(self) -> None:
        svc, _dp, cats, cookies = self._pix_mgmt_session()
        pix_id = self._pix_add("Beta", "ALAN MOURA", "52998224725", "CPF")
        self._pix_mgmt_post(f"/dp/pix/{pix_id}/atualizar", cookies, name="ALAN MOURA", key="a@b.com", key_type="E-mail")
        _, page, _, _ = self._pix_mgmt_post(f"/dp/pix/{pix_id}/excluir", cookies)
        self.assertIn("excluído", page)
        self.assertIsNone(self._pix_row("ALAN MOURA"))
        with self.app_db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM pix_key_history").fetchone()["n"], 0)
        _, page, _, _ = self._pix_mgmt_post(f"/dp/pix/{pix_id}/excluir", cookies)
        self.assertIn("não encontrado", page)

    def test_gestao_pix_csrf_e_escape(self) -> None:
        svc, _dp, cats, cookies = self._pix_mgmt_session()
        pix_id = self._pix_add("Beta", "ALAN MOURA", "52998224725", "CPF")
        from urllib.parse import urlencode
        _, page, _, _ = self.request_http(
            "POST", f"/dp/pix/{pix_id}/excluir", urlencode({"csrf_token": "errado"}),
            {**cookies, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertIn("Sessão do formulário expirada", page)
        self.assertIsNotNone(self._pix_row("ALAN MOURA"))
        self._pix_mgmt_post("/dp/pix/adicionar", cookies, name="<script>x</script>", key="<b>k</b>", origin_id=str(cats["Beta"]))
        _, page, _, _ = self.request_http("GET", "/dp/pix", headers=cookies)
        self.assertNotIn("<SCRIPT>X", page)
        self.assertNotIn("<b>k</b>", page)
        self.assertIn("&lt;b&gt;k&lt;/b&gt;", page)


if __name__ == "__main__":
    unittest.main()
