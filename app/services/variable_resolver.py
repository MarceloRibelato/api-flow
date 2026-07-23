"""
Variable Resolver — Handles {{variable}} substitution in flow execution.
Extracted from flow_executor_service.py for modularity and testability.
"""
import re
import logging
import random
import string
import uuid
import datetime

try:
    from faker import Faker
    fake = Faker('pt_BR')
except ImportError:
    fake = None
    logging.getLogger(__name__).warning("Faker library not installed. Dynamic variables will fallback to static values.")

logger = logging.getLogger(__name__)

def _generate_faker_value(faker_type: str, faker_options: dict) -> str:
    """Generates a dynamic value using faker based on type and options."""
    if not fake:
        return "Faker Not Installed"
        
    if not faker_options:
        faker_options = {}

    if faker_type == 'firstName':
        return fake.first_name()
    elif faker_type == 'lastName':
        return fake.last_name()
    elif faker_type == 'fullName':
        return fake.name()
    elif faker_type == 'email':
        return fake.email()
    elif faker_type == 'cpf':
        formatted = faker_options.get('formatted', True)
        cpf_val = fake.cpf()
        if not formatted:
            cpf_val = cpf_val.replace('.', '').replace('-', '')
        return cpf_val
    elif faker_type == 'cnpj':
        formatted = faker_options.get('formatted', True)
        cnpj_val = fake.cnpj()
        if not formatted:
            cnpj_val = cnpj_val.replace('.', '').replace('-', '').replace('/', '')
        return cnpj_val
    elif faker_type == 'cnpj_alpha':
        part1 = "".join(random.choices(string.ascii_uppercase + string.digits, k=2))
        part2 = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
        part3 = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
        branch = "0001"
        digits = "".join(random.choices(string.digits, k=2))
        formatted = faker_options.get('formatted', True)
        if formatted:
            return f"{part1}.{part2}.{part3}/{branch}-{digits}"
        return f"{part1}{part2}{part3}{branch}{digits}"
    elif faker_type == 'phone':
        formatted = faker_options.get('formatted', True)
        country_code = faker_options.get('countryCode', True)
        ddd = str(random.randint(11, 99))
        p1 = str(random.randint(90000, 99999))
        p2 = str(random.randint(1000, 9999))
        if formatted:
            if country_code:
                return f"+55 ({ddd}) {p1}-{p2}"
            return f"({ddd}) {p1}-{p2}"
        return f"{'55' if country_code else ''}{ddd}{p1}{p2}"
    elif faker_type == 'city':
        return fake.city()
    elif faker_type == 'uuid':
        return str(uuid.uuid4())
    elif faker_type == 'password':
        return fake.password()
    elif faker_type == 'date':
        direction = faker_options.get('direction', 'future')
        mask = faker_options.get('mask', 'YYYY-MM-DD')
        dt = fake.future_datetime() if direction == 'future' else fake.past_datetime()
        mapping = {'YYYY': '%Y', 'MM': '%m', 'DD': '%d', 'HH': '%H', 'mm': '%M', 'ss': '%S'}
        for k, v in mapping.items():
            mask = mask.replace(k, v)
        return dt.strftime(mask)
    elif faker_type == 'number':
        length = int(faker_options.get('length', 4))
        return "".join(random.choices(string.digits, k=length))
        
    return fake.word()


def replace_vars(text: str, variables: dict) -> str:
    """
    Replaces {{VARIABLE_NAME}} placeholders in text with values from the variables dict.
    
    The variables dict can contain:
      - Direct string values: {"VAR_NAME": "value"}
      - ORM objects with .name and .value attributes
      - Dictionaries with 'name' and 'value' keys
    
    Matching is case-insensitive (normalized to UPPER).
    """
    if not text or not isinstance(text, str):
        return text

    def get_attr(obj, attr, default=None):
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    def has_attr(obj, attr):
        if isinstance(obj, dict):
            return attr in obj
        return hasattr(obj, attr)

    def set_attr(obj, attr, value):
        if isinstance(obj, dict):
            obj[attr] = value
        else:
            setattr(obj, attr, value)

    def replacer(match):
        var_name = match.group(1).strip().upper()

        # 1. Direct Lookup (fastest path)
        if var_name in variables:
            val = variables[var_name]
            
            # If it's a Variable object/dict and has a faker_type, generate it dynamically
            if has_attr(val, 'type') and get_attr(val, 'type') == 'faker' and get_attr(val, 'faker_type'):
                if not has_attr(val, '_generated_value'):
                    dynamic_val = _generate_faker_value(get_attr(val, 'faker_type'), get_attr(val, 'faker_options', {}))
                    set_attr(val, '_generated_value', dynamic_val)
                    logger.debug(f"Generated and cached dynamic faker value for '{var_name}': {dynamic_val}")
                return str(get_attr(val, '_generated_value'))
                
            if has_attr(val, 'value'):
                return str(get_attr(val, 'value'))
            return str(val)

        # 2. Fallback: scan objects by .name attribute
        for v in variables.values():
            if has_attr(v, 'name') and get_attr(v, 'name').upper() == var_name:
                if has_attr(v, 'type') and get_attr(v, 'type') == 'faker' and get_attr(v, 'faker_type'):
                    if not has_attr(v, '_generated_value'):
                        dynamic_val = _generate_faker_value(get_attr(v, 'faker_type'), get_attr(v, 'faker_options', {}))
                        set_attr(v, '_generated_value', dynamic_val)
                        logger.debug(f"Generated and cached dynamic faker value for '{var_name}' (Fallback Match): {dynamic_val}")
                    return str(get_attr(v, '_generated_value'))
                    
                logger.debug(f"Replaced '{match.group(1)}' with '{get_attr(v, 'value')}' (Fallback Match)")
                return str(get_attr(v, 'value'))

        # 3. Not found — keep original placeholder
        logger.warning(f"Variable '{var_name}' NOT FOUND. Available: {list(variables.keys())}")
        return match.group(0)

    return re.sub(r'\{\{([\w\.\-_]+)\}\}', replacer, text)
