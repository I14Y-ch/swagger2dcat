import requests
import json

def get_agents():
    """
    Fetch agents from the I14Y Admin API and enrich with address data from Staatskalender API if missing.
    Returns a list of agent dictionaries with id, name, and address.
    """
    try:
        # Fetch agents from I14Y API
        response = requests.get('https://input.i14y.admin.ch/api/Agent', timeout=10)
        response.raise_for_status()
        agents_data = response.json()

        # Process agents to include display name and address
        processed_agents = []
        for agent in agents_data:
            # Skip agents without id or name
            if not agent.get('id') or not agent.get('name'):
                continue

            # Use English name if available, otherwise German, or any available language
            display_name = None
            if 'en' in agent['name'] and agent['name']['en']:
                display_name = agent['name']['en']
            elif 'de' in agent['name'] and agent['name']['de']:
                display_name = agent['name']['de']
            else:
                # Use first non-empty name value
                for lang, name in agent['name'].items():
                    if name:
                        display_name = name
                        break

            # Skip if no display name could be found
            if not display_name:
                continue

            # Initialize address data
            address = None

            # If no address is available, fetch from Staatskalender API
            if 'de' in agent['name'] and agent['name']['de']:
                german_name = agent['name']['de']
                try:
                    staatskalender_response = requests.get(
                        f"https://www.staatskalender.admin.ch/api/search/organizations?lang=de&s={german_name}&page=1&pageSize=1",
                        timeout=10
                    )
                    staatskalender_response.raise_for_status()
                    staatskalender_data = staatskalender_response.json()

                    if staatskalender_data.get('result'):
                        org_data = staatskalender_data['result'][0]
                        address = {
                            'phone': org_data.get('phone', ''),
                            'email': org_data.get('email', ''),
                            'department': org_data.get('department', {}).get('name', {}),
                            'organization': org_data.get('organization', {}).get('name', {})
                        }
                except Exception as e:
                    pass  # Skip errors silently for optional address fetching

            processed_agents.append({
                'id': agent['id'],
                'display_name': display_name,
                'name': agent['name'],  # Keep full name dictionary for reference
                'address': address  # Include address if available
            })

        # Sort agents by display name
        processed_agents.sort(key=lambda x: x['display_name'])

        return processed_agents

    except Exception as e:
        return []