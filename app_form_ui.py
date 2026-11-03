from __future__ import annotations

import html

from app_services import (
    ADMISSION_FIELD_SPECS,
    DEPENDENT_FIELD_SPECS,
    RESCISSION_EVENT_FIELD_RE,
    RESCISSION_EVENT_NAME_MAX,
    RESCISSION_EVENT_VALUE_MAX,
    VACATION_FIELD_SPECS,
    field_error,
)


ADMISSION_FIELD_GROUPS = (
    (
        "Dados da empresa",
        "Empresa",
        "Identifique o contexto da contratação dentro da operação.",
        ("employee_company_unit_type", "employee_worksite"),
    ),
    (
        "Dados funcionais",
        "Função",
        "Defina cargo, admissão, jornada, benefícios e regras operacionais.",
        (
            "employee_name",
            "employee_cpf",
            "employee_admission_date",
            "employee_birth_date",
            "employee_role",
            "employee_cbo",
            "employee_department",
            "employee_cost_center",
            "employee_salary",
            "employee_work_schedule",
            "employee_work_interval",
            "employee_experience_contract",
            "employee_experience_contract_other",
            "employee_insalubrity",
            "employee_insalubrity_percentage",
            "employee_hazard_pay",
            "employee_advance_enabled",
            "employee_transport_voucher",
            "employee_meal_voucher",
        ),
    ),
    (
        "Dados pessoais",
        "Pessoal",
        "Informações cadastrais, endereço, filiação e naturalidade do funcionário.",
        (
            "employee_ethnicity",
            "employee_gender",
            "employee_education",
            "employee_zip_code",
            "employee_address",
            "employee_address_number",
            "employee_city",
            "employee_neighborhood",
            "employee_state",
            "employee_phone",
            "employee_father_name",
            "employee_mother_name",
            "employee_birth_city",
            "employee_birth_state",
            "employee_marital_status",
        ),
    ),
    (
        "Documentação",
        "Documentos",
        "Dados civis e documentos trabalhistas usados no registro admissional.",
        (
            "employee_pis",
            "employee_rg",
            "employee_rg_issuer",
            "employee_rg_issuer_state",
            "employee_rg_issue_date",
            "employee_work_card_type",
            "employee_work_card_number",
            "employee_work_card_series",
            "employee_work_card_state",
            "employee_work_card_issue_date",
            "employee_voter_id",
            "employee_voter_zone",
            "employee_voter_section",
        ),
    ),
    (
        "Pensão alimentícia",
        "Pensão",
        "Preencha apenas quando houver retenção judicial e dados de depósito do beneficiário.",
        (
            "employee_has_alimony",
            "employee_alimony_percentage",
            "employee_alimony_beneficiary_name",
            "employee_alimony_beneficiary_cpf",
            "employee_alimony_bank",
            "employee_alimony_agency",
            "employee_alimony_account",
        ),
    ),
    (
        "Informações bancárias",
        "Banco",
        "Dados bancários para remessa, conta e chave Pix do funcionário.",
        (
            "employee_bank_name",
            "employee_bank_account_type",
            "employee_bank_account_number",
            "employee_bank_agency",
            "employee_pix_key",
            "employee_pix_key_type",
        ),
    ),
    (
        "Observações",
        "Ajustes",
        "Use este espaço para instruções complementares relevantes para a admissão.",
        ("employee_notes",),
    ),
)


def render_select_options(options: tuple[str, ...], selected: str, placeholder: str = "Selecione") -> str:
    return f'<option value="">{html.escape(placeholder)}</option>' + "".join(
        f'<option value="{html.escape(value)}" {"selected" if selected == value else ""}>{html.escape(value)}</option>'
        for value in options
    )


def render_admission_fields(form_data: dict[str, str], errors: dict[str, str]) -> str:
    fields_by_name = {field["name"]: field for field in ADMISSION_FIELD_SPECS}

    def render_field(field: dict[str, str]) -> str:
        name = field["name"]
        label = field["label"]
        value = form_data.get(name, "")
        input_type = field.get("type", "text")
        options = field.get("options")
        required_attr = " required" if field.get("required") else ""
        invalid_attr = "true" if errors.get(name) else "false"
        data_attrs = ""
        show_when = field.get("show_when") or {}
        required_when = field.get("required_when") or {}
        if show_when:
            dep_name, dep_value = next(iter(show_when.items()))
            data_attrs += f' data-show-when-field="{html.escape(dep_name)}" data-show-when-value="{html.escape(dep_value)}"'
        if required_when:
            dep_name, dep_value = next(iter(required_when.items()))
            data_attrs += f' data-required-when-field="{html.escape(dep_name)}" data-required-when-value="{html.escape(dep_value)}"'

        if options:
            control_html = (
                f'<select name="{html.escape(name)}" aria-invalid="{invalid_attr}"{required_attr}>'
                f"{render_select_options(options, value)}"
                "</select>"
            )
        elif input_type == "textarea":
            control_html = (
                f'<textarea name="{html.escape(name)}" rows="4" aria-invalid="{invalid_attr}"{required_attr}>'
                f"{html.escape(value)}"
                "</textarea>"
            )
        else:
            extra_attrs = ""
            if name in {"employee_cpf", "employee_alimony_beneficiary_cpf"}:
                extra_attrs = ' inputmode="numeric" maxlength="14" placeholder="___.___.___-__" data-mask="cpf"'
            elif name == "employee_zip_code":
                extra_attrs = ' inputmode="numeric" maxlength="9" placeholder="_____-___" data-mask="cep"'
            elif name == "employee_phone":
                extra_attrs = ' inputmode="tel" maxlength="15" placeholder="(__) _____-____" data-mask="phone"'
            elif name == "employee_pis":
                extra_attrs = ' inputmode="numeric" maxlength="14" placeholder="___._____.__-_" data-mask="pis"'
            elif name == "employee_salary":
                extra_attrs = ' inputmode="decimal" placeholder="0,00" data-mask="currency"'
            elif name == "employee_rg":
                extra_attrs = ' maxlength="20" data-mask="rg"'
            elif name in {"employee_state", "employee_birth_state", "employee_rg_issuer_state", "employee_work_card_state"}:
                extra_attrs = ' maxlength="2" data-transform="uppercase"'
            control_html = (
                f'<input name="{html.escape(name)}" type="{html.escape(input_type)}" '
                f'value="{html.escape(value)}" aria-invalid="{invalid_attr}"{required_attr}{extra_attrs}>'
            )

        span_class = " field--span-full" if input_type == "textarea" else ""
        return f"""
            <label class="field {'field--error' if errors.get(name) else ''}{span_class}"{data_attrs}>
              <span>{html.escape(label)}</span>
              {control_html}
              {field_error(errors, name)}
            </label>
            """

    sections_html: list[str] = []
    for title, eyebrow, description, field_names in ADMISSION_FIELD_GROUPS:
        section_fields = [fields_by_name[name] for name in field_names if name in fields_by_name]
        if not section_fields:
            continue
        fields_html = "".join(render_field(field) for field in section_fields)
        sections_html.append(
            f"""
            <section class="form-section form-section--admission">
              <div class="form-section__header">
                <div class="form-section__divider"></div>
                <div class="section-heading section-heading--stacked">
                  <div>
                    <p class="section-heading__eyebrow">{html.escape(eyebrow)}</p>
                    <h3>{html.escape(title)}</h3>
                  </div>
                  <p class="section-heading__text">{html.escape(description)}</p>
                </div>
              </div>
              <div class="field-grid field-grid--admission">
                {fields_html}
              </div>
            </section>
            """
        )

    return f"""
    <div class="admission-fields">
      <div class="section-heading section-heading--stacked">
        <div>
          <p class="section-heading__eyebrow">Admissão</p>
          <h2>Dados do funcionário</h2>
        </div>
        <p class="section-heading__text">Preencha os dados obrigatórios para concluir a solicitação de admissão.</p>
      </div>
      {''.join(sections_html)}
      {render_dependent_section(form_data, errors)}
    </div>
    """


def render_vacation_fields(form_data: dict[str, str], errors: dict[str, str]) -> str:
    fields_html: list[str] = []
    for field in VACATION_FIELD_SPECS:
        name = field["name"]
        label = field["label"]
        value = form_data.get(name, "")
        input_type = field.get("type", "text")
        options = field.get("options")
        required_attr = " required" if field.get("required") else ""
        invalid_attr = "true" if errors.get(name) else "false"
        data_attrs = ""
        show_when = field.get("show_when") or {}
        required_when = field.get("required_when") or {}
        if show_when:
            dep_name, dep_value = next(iter(show_when.items()))
            data_attrs += f' data-show-when-field="{html.escape(dep_name)}" data-show-when-value="{html.escape(dep_value)}"'
        if required_when:
            dep_name, dep_value = next(iter(required_when.items()))
            data_attrs += f' data-required-when-field="{html.escape(dep_name)}" data-required-when-value="{html.escape(dep_value)}"'
        if options:
            control_html = (
                f'<select name="{html.escape(name)}" aria-invalid="{invalid_attr}"{required_attr}>'
                f"{render_select_options(options, value)}"
                "</select>"
            )
        else:
            extra_attrs = ' min="1"' if input_type == "number" else ""
            if name == "vacation_start_date":
                extra_attrs += ' data-weekday-limit="mon-wed"'
            if field.get("max_length"):
                extra_attrs += f' maxlength="{field["max_length"]}"'
            control_html = (
                f'<input name="{html.escape(name)}" type="{html.escape(input_type)}" '
                f'value="{html.escape(value)}" aria-invalid="{invalid_attr}"{required_attr}{extra_attrs}>'
            )
        fields_html.append(
            f"""
            <label class="field {'field--error' if errors.get(name) else ''}"{data_attrs}>
              <span>{html.escape(label)}</span>
              {control_html}
              {field_error(errors, name)}
            </label>
            """
        )
    return f"""
    <div class="admission-fields">
      <div class="section-heading section-heading--stacked">
        <div>
          <p class="section-heading__eyebrow">Férias</p>
          <h2>Dados das férias</h2>
        </div>
        <p class="section-heading__text">Preencha os dados para registrar a solicitação de férias.</p>
      </div>
      <div class="field-grid field-grid--admission">
        {''.join(fields_html)}
      </div>
    </div>
    """


def collect_dependent_entries(form_data: dict[str, str], errors: dict[str, str]) -> list[dict[str, str]]:
    indices: set[int] = set()
    for source in (form_data, errors):
        for key in source.keys():
            if not key.startswith("dependent_"):
                continue
            parts = key.split("_", 2)
            if len(parts) < 3:
                continue
            try:
                indices.add(int(parts[1]))
            except ValueError:
                continue
    return [
        {field["name"]: form_data.get(f"dependent_{index}_{field['name']}", "") for field in DEPENDENT_FIELD_SPECS}
        for index in sorted(indices)
    ]


def render_dependent_card(index: int, values: dict[str, str], errors: dict[str, str]) -> str:
    field_html: list[str] = []
    for field_index, field in enumerate(DEPENDENT_FIELD_SPECS):
        full_name = f"dependent_{index}_{field['name']}"
        value = values.get(field["name"], "")
        invalid_attr = "true" if errors.get(full_name) else "false"
        options = field.get("options")
        if options:
            control_html = (
                f'<select name="{html.escape(full_name)}" aria-invalid="{invalid_attr}" required>'
                f"{render_select_options(options, value)}"
                "</select>"
            ) if field.get("required") else (
                f'<select name="{html.escape(full_name)}" aria-invalid="{invalid_attr}">'
                f"{render_select_options(options, value)}"
                "</select>"
            )
        else:
            extra_attrs = ""
            if field["name"] == "cpf":
                extra_attrs = ' inputmode="numeric" maxlength="14" placeholder="___.___.___-__" data-mask="cpf"'
            required_attr = " required" if field.get("required") else ""
            control_html = (
                f'<input name="{html.escape(full_name)}" type="{html.escape(field.get("type", "text"))}" '
                f'value="{html.escape(value)}" aria-invalid="{invalid_attr}"{required_attr}{extra_attrs}>'
            )
        field_html.append(
            f"""
            <label class="field {'field--error' if errors.get(full_name) else ''}">
              <span>{html.escape(field['label'])}</span>
              {control_html}
              {field_error(errors, full_name)}
            </label>
            """
        )
        if field_index == 4:
            field_html.append(
                """
                <p class="dependent-card__inline-note"><em>Se for dependente de salário família, preencher abaixo:</em></p>
                """
            )

    return f"""
    <section class="dependent-card" data-dependent-card>
      <div class="section-heading section-heading--stacked">
        <div>
          <p class="section-heading__eyebrow">Dependente {index + 1}</p>
          <h3>Dados do dependente</h3>
        </div>
      </div>
      <div class="field-grid field-grid--admission">
        {''.join(field_html)}
      </div>
      <button class="button button--ghost dependent-card__remove" type="button" data-remove-dependent>Remover dependente</button>
    </section>
    """


def render_dependent_section(form_data: dict[str, str], errors: dict[str, str]) -> str:
    entries = collect_dependent_entries(form_data, errors)
    cards_html = "".join(render_dependent_card(index, values, errors) for index, values in enumerate(entries))
    template_html = render_dependent_card(0, {}, {})
    return f"""
    <div class="dependent-section" data-dependent-section>
      <input type="hidden" name="dependent_count" value="{len(entries)}" data-dependent-count>
      <div class="dependent-list" data-dependent-list>
        {cards_html}
      </div>
      <button class="button button--primary" type="button" data-add-dependent>Incluir dependente</button>
      <template data-dependent-template>{template_html}</template>
    </div>
    """


def collect_rescission_event_entries(form_data: dict[str, str]) -> list[dict[str, str]]:
    indices = sorted({int(match.group(1)) for match in map(RESCISSION_EVENT_FIELD_RE.fullmatch, form_data) if match})
    return [
        {
            "name": form_data.get(f"rescission_event_{index}_name", ""),
            "value": form_data.get(f"rescission_event_{index}_value", ""),
        }
        for index in indices
    ]


def render_rescission_event_row(index: int, values: dict[str, str], errors: dict[str, str]) -> str:
    name_key = f"rescission_event_{index}_name"
    value_key = f"rescission_event_{index}_value"
    return f"""
    <div class="rescission-event" data-rescission-event>
      <label class="field {'field--error' if errors.get(name_key) else ''}">
        <span>Evento</span>
        <input name="{name_key}" type="text" maxlength="{RESCISSION_EVENT_NAME_MAX}" value="{html.escape(values.get('name', ''), quote=True)}" aria-invalid="{'true' if errors.get(name_key) else 'false'}">
        {field_error(errors, name_key)}
      </label>
      <label class="field {'field--error' if errors.get(value_key) else ''}">
        <span>Valor</span>
        <input name="{value_key}" type="text" maxlength="{RESCISSION_EVENT_VALUE_MAX}" value="{html.escape(values.get('value', ''), quote=True)}" aria-invalid="{'true' if errors.get(value_key) else 'false'}">
        {field_error(errors, value_key)}
      </label>
      <button class="button button--ghost rescission-event__remove" type="button" aria-label="Excluir evento" title="Excluir evento" data-remove-rescission-event>&times;</button>
    </div>
    """


def render_rescission_events_section(form_data: dict[str, str], errors: dict[str, str]) -> str:
    entries = collect_rescission_event_entries(form_data)
    rows_html = "".join(render_rescission_event_row(index, values, errors) for index, values in enumerate(entries))
    return f"""
    <div class="rescission-events {'field--error' if errors.get('rescission_events') else ''}" data-rescission-events>
      <span class="field-label field-label--strong">Eventos</span>
      <input type="hidden" value="{len(entries)}" data-rescission-event-count>
      <div class="rescission-events__list" data-rescission-event-list>
        {rows_html}
      </div>
      {field_error(errors, 'rescission_events')}
      <button class="button button--primary rescission-events__add" type="button" data-add-rescission-event>Incluir evento</button>
      <template data-rescission-event-template>{render_rescission_event_row(0, {}, {})}</template>
    </div>
    """


def render_public_form_script() -> str:
    return """
            <script>
              (function() {
                const form = document.querySelector('form[action="/abrir-chamado"]');
                if (!form) return;
                const requestCategory = form.querySelector('[data-request-category]');
                const genericFields = form.querySelector('.generic-request-fields');
                const admissionContainer = form.querySelector('[data-admission-container]');
                const vacationContainer = form.querySelector('[data-vacation-container]');
                const vacationSubcategory = form.querySelector('[name="vacation_subcategory"]');
                const vacationStartDate = form.querySelector('[name="vacation_start_date"]');
                const genericRequiredFields = form.querySelectorAll('[data-generic-required]');
                const conditionalFields = form.querySelectorAll('[data-show-when-field], [data-required-when-field]');
                const dependentSection = form.querySelector('[data-dependent-section]');
                const dependentList = form.querySelector('[data-dependent-list]');
                const dependentCountInput = form.querySelector('[data-dependent-count]');
                const dependentTemplate = form.querySelector('[data-dependent-template]');

                function applyCpfMask(value) {
                  const digits = value.replace(/\\D/g, '').slice(0, 11);
                  if (digits.length <= 3) return digits;
                  if (digits.length <= 6) return `${digits.slice(0, 3)}.${digits.slice(3)}`;
                  if (digits.length <= 9) return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6)}`;
                  return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6, 9)}-${digits.slice(9)}`;
                }

                function applyCepMask(value) {
                  const digits = value.replace(/\\D/g, '').slice(0, 8);
                  if (digits.length <= 5) return digits;
                  return `${digits.slice(0, 5)}-${digits.slice(5)}`;
                }

                function applyPhoneMask(value) {
                  const digits = value.replace(/\\D/g, '').slice(0, 11);
                  if (digits.length <= 2) return digits;
                  if (digits.length <= 6) return `(${digits.slice(0, 2)}) ${digits.slice(2)}`;
                  if (digits.length <= 10) return `(${digits.slice(0, 2)}) ${digits.slice(2, 6)}-${digits.slice(6)}`;
                  return `(${digits.slice(0, 2)}) ${digits.slice(2, 7)}-${digits.slice(7)}`;
                }

                function applyPisMask(value) {
                  const digits = value.replace(/\\D/g, '').slice(0, 11);
                  if (digits.length <= 3) return digits;
                  if (digits.length <= 8) return `${digits.slice(0, 3)}.${digits.slice(3)}`;
                  if (digits.length <= 10) return `${digits.slice(0, 3)}.${digits.slice(3, 8)}.${digits.slice(8)}`;
                  return `${digits.slice(0, 3)}.${digits.slice(3, 8)}.${digits.slice(8, 10)}-${digits.slice(10)}`;
                }

                function applyCurrencyMask(value) {
                  const digits = value.replace(/\\D/g, '');
                  if (!digits) return '';
                  const integerPart = String(Number(digits.slice(0, -2) || '0'));
                  const decimalPart = digits.slice(-2).padStart(2, '0');
                  return `${integerPart},${decimalPart}`;
                }

                function applyRgMask(value) {
                  return value.replace(/[^0-9A-Za-z]/g, '').toUpperCase().slice(0, 20);
                }

                function bindMaskedFields(scope) {
                  const maskHandlers = {
                    cpf: applyCpfMask,
                    cep: applyCepMask,
                    phone: applyPhoneMask,
                    pis: applyPisMask,
                    currency: applyCurrencyMask,
                    rg: applyRgMask,
                  };
                  scope.querySelectorAll('[data-mask]').forEach((field) => {
                    if (field.dataset.maskBound === '1') return;
                    const handler = maskHandlers[field.dataset.mask];
                    if (!handler) return;
                    field.addEventListener('input', () => {
                      field.value = handler(field.value);
                    });
                    field.value = handler(field.value);
                    field.dataset.maskBound = '1';
                  });
                  scope.querySelectorAll('[data-transform="uppercase"]').forEach((field) => {
                    if (field.dataset.transformBound === '1') return;
                    field.addEventListener('input', () => {
                      field.value = field.value.toUpperCase().slice(0, 2);
                    });
                    field.value = field.value.toUpperCase().slice(0, 2);
                    field.dataset.transformBound = '1';
                  });
                }

                function updateDependentHeadings() {
                  if (!dependentList) return;
                  Array.from(dependentList.querySelectorAll('[data-dependent-card]')).forEach((card, index) => {
                    const eyebrow = card.querySelector('.section-heading__eyebrow');
                    if (eyebrow) eyebrow.textContent = `Dependente ${index + 1}`;
                  });
                }

                function addDependent() {
                  if (!dependentList || !dependentTemplate || !dependentCountInput) return;
                  const nextIndex = Number(dependentCountInput.value || '0');
                  const html = dependentTemplate.innerHTML.replaceAll('dependent_0_', `dependent_${nextIndex}_`);
                  const wrapper = document.createElement('div');
                  wrapper.innerHTML = html.trim();
                  const card = wrapper.firstElementChild;
                  if (!card) return;
                  dependentList.appendChild(card);
                  dependentCountInput.value = String(nextIndex + 1);
                  bindMaskedFields(card);
                  updateDependentHeadings();
                }

                function addRescissionEvent() {
                  const list = form.querySelector('[data-rescission-event-list]');
                  const template = form.querySelector('[data-rescission-event-template]');
                  const count = form.querySelector('[data-rescission-event-count]');
                  if (!list || !template || !count) return;
                  const nextIndex = Number(count.value || '0');
                  const wrapper = document.createElement('div');
                  wrapper.innerHTML = template.innerHTML.replaceAll('rescission_event_0_', `rescission_event_${nextIndex}_`).trim();
                  const row = wrapper.firstElementChild;
                  if (!row) return;
                  list.appendChild(row);
                  count.value = String(nextIndex + 1);
                  const firstInput = row.querySelector('input');
                  if (firstInput) firstInput.focus();
                }

                function validateVacationStartDate() {
                  if (!vacationStartDate || !vacationStartDate.value) return;
                  const parts = vacationStartDate.value.split('-').map(Number);
                  if (parts.length !== 3 || parts.some(Number.isNaN)) return;
                  const weekday = new Date(parts[0], parts[1] - 1, parts[2]).getDay();
                  const blockedDays = new Set([4, 5, 6, 0]);
                  if (blockedDays.has(weekday)) {
                    vacationStartDate.setCustomValidity('O início das férias deve ser entre segunda e quarta-feira.');
                    vacationStartDate.reportValidity();
                    vacationStartDate.value = '';
                    return;
                  }
                  vacationStartDate.setCustomValidity('');
                }

                function updateAdmissionVisibility() {
                  const selectedCategory = requestCategory ? requestCategory.value : '';
                  const isAdmission = selectedCategory === 'Admissão';
                  const isVacation = selectedCategory === 'Férias';
                  const isAvisoFerias = isVacation && !!vacationSubcategory && vacationSubcategory.value === 'Aviso de férias';
                  if (genericFields) genericFields.classList.toggle('is-hidden', isAdmission || (isVacation && !isAvisoFerias));
                  if (admissionContainer) admissionContainer.classList.toggle('is-hidden', !isAdmission);
                  if (vacationContainer) vacationContainer.classList.toggle('is-hidden', !isVacation || isAvisoFerias);
                  if (dependentSection) dependentSection.classList.toggle('is-hidden', !isAdmission);
                  genericRequiredFields.forEach((field) => {
                    field.required = !isAdmission && (!isVacation || isAvisoFerias);
                  });

                  conditionalFields.forEach((wrapper) => {
                    const showFieldName = wrapper.dataset.showWhenField;
                    const showFieldValue = wrapper.dataset.showWhenValue;
                    const showField = showFieldName ? form.querySelector(`[name="${showFieldName}"]`) : null;
                    const shouldShow = !showField || showField.value === showFieldValue;
                    const shouldShowStructured = isAdmission || (isVacation && !isAvisoFerias);
                    wrapper.classList.toggle('is-hidden', !shouldShowStructured || !shouldShow);

                    const control = wrapper.querySelector('input, select, textarea');
                    if (!control) return;
                    const requiredFieldName = wrapper.dataset.requiredWhenField;
                    const requiredFieldValue = wrapper.dataset.requiredWhenValue;
                    const requiredField = requiredFieldName ? form.querySelector(`[name="${requiredFieldName}"]`) : null;
                    control.required = shouldShowStructured && !!requiredField && requiredField.value === requiredFieldValue;
                  });
                }

                if (requestCategory) {
                  requestCategory.addEventListener('change', updateAdmissionVisibility);
                }
                if (vacationStartDate) {
                  vacationStartDate.addEventListener('change', validateVacationStartDate);
                }
                form.addEventListener('click', (event) => {
                  const addButton = event.target.closest('[data-add-dependent]');
                  if (addButton) {
                    addDependent();
                    return;
                  }
                  if (event.target.closest('[data-add-rescission-event]')) {
                    addRescissionEvent();
                    return;
                  }
                  const removeEventButton = event.target.closest('[data-remove-rescission-event]');
                  if (removeEventButton) {
                    const eventRow = removeEventButton.closest('[data-rescission-event]');
                    if (eventRow) eventRow.remove();
                    return;
                  }
                  const removeButton = event.target.closest('[data-remove-dependent]');
                  if (removeButton) {
                    const card = removeButton.closest('[data-dependent-card]');
                    if (card) {
                      card.remove();
                      updateDependentHeadings();
                    }
                  }
                });
                bindMaskedFields(form);
                form.addEventListener('change', updateAdmissionVisibility);
                updateDependentHeadings();
                updateAdmissionVisibility();
              })();
            </script>
    """
