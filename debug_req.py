import requests
import json

url = "https://api.veloe.com.br/vsg-sdk-bff/v1/autenticacao/login"

headers = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "VSG-PUBLIC-KEY": "8d9178fe-020a-46ae-b96e-59e6593257b3",
    "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "vsg-dispositivo": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "vsg-linguagem": "en-US",
    "vsg-timezone": "America/Sao_Paulo"
}

data = {
    "login": "19479537028", 
    "password": "Teste@123456", 
    "recaptchaAction": "login", 
    "recaptchaToken": "0cAFcWeA6kNSPtnVxhMVetEaxDfMfQm2xzhhakXL4QAy7QAIOOhMPPqyF6K2wLNPkZZFrOYBm0k__fS5_m9dU6vEhitj_DytwTJIkDVZ1VFZ7BvCzgaCIajfiWViWdZ1GeVuh1cC6IBpR8WIlVYrDYm1cwcssEG8_8O72qPN6hKUFcKpzBIWIIvBIKK8AB934Ufz5_lFn5QZqDL-ZU8QJmBMNENnD08uBlSkA0B-66nQDdpBdonObN0BdSHyXHxFrdz3imRUwSRfHX2UI1iurJUQCKaLYgFv5WOkYe56DOl-GloCZb4RSWto7m7jbe2sncUmCCqp93xjcZhLQYvcgow8W8bsN9iGOC5miYy0vZfGX_l3SqZ3GZtJdp3R70nilYYp3SWRRw75cQ5d192o9ZptpWaGOsmfe6TEv4mXDZ2gQFrWGG6_u-VD8tXG3TP55HJIkAmrhuXQS_lNIgj2QKcB79bQQ04KnNjXqpIQhHnIlbYmquTnOnSimeW-NfsrF_wWLICF__BURYxlb9HYvoZT7Xrj7sMPOO5wJBqnKdvmTDt8bWfoB0x3SmOI4pkOXi_tzeNWz1C0FfrZ4j5UuCPwDo0qGZKDlJYn3KD0UxYd7MOkmJNlRmV1u0NSqWkSloM0d9p7EQcZnIZRcHQu3IL2LHqxacLSuB-FcVE_s305Sva9RAtySTK7bJU4Iep6JmdaomFkHFv3RTjT9wIN1l4MI787e1_ds96N0p5jANYzKlufN3ZxCemneKHX7MXKkDoUIz9OAve769OYyZ9oxQtrANiZB1YryrAhb3zfvw6Eu1vfqhX3WmHcGYL75Xje-nrHhPAivgAWn0-9ma-g2NV8JGTFQWwFecJ1q4JeN_qYPGHorj0FgH_a0OOpj29oJwEOf1VZUoo3KWZX7t7vWc8Vgn1X2rinjdS20sfSRUWp1VSh-ItVMBPGhLhYdQnaou4VRgHQoE42kzWM-8imQeDYgAxBZXJPopFcv3l7Mr184WWSuGyGFta5LzG9rvPP5GOcCzV5MhGmY5tC1eKwmK46GUSFDGEQ4sW_QL_h4crujgkyeUL4WF7F_-1u6kMBi9VG-ywIaB2bxj1gfLXApqhudpn5tYJsFp6qo_h2P1c70hDKCM_aaYm5Mjyd45CGCPTjsGVxh5yBsqh7pxfK9OwRRvHVmuodi4h5oy26gf0Y2YvV3jj5iudZKYkbw0EdfEylWgObzUVVssF_UZVwmZXk80KyIltjR1a9oN8f6Qa39SPVFr7VkXwxaaUn3l8fFMS7Ak_aNeCq5-A2_NjRRO6D3EtGgttO_jrXxtoHplEF4EaOYT7R6A6o-VtxWskddYykjpn74u1JhID-Def1e54h5bSVhY1gQ7KX9iBy8riG84EvWMaTsT6g6E6_B8Pl4rcFIGMZh__V1o_fc-wOVNT82px6XDT7XsE6xuLBDpA3RWAPx4U4Q34TFMx_7fU4cS3kOx0CSdV-Aizfl2c7wOyLj5DqMeAGVoII8SkgI3W7pgsTLamGMnD0HdNVl5bvdkjGIww2cJ1FS1C-CYRKxtfj59_6sWR7BU_AI7VR8d5Eo34sXr5Y2wkDiH0gTlcCe6Dim5TMJzvFKUqRdJ8S8z5GcFqhIWBJuBejNFu8F18z5b_3r_r-Jd-nAx6vgMVk4Qy-qJqdIzPN9fLzrfuEu7B-aulX1CaJurjQWdqbXF114WCw1v72iWLZ2N9xHaWkgHH-wxmNlBeZFMiJl2Xb3uCvC_EoaWtBtISueBa7eUV6Ji1IvKMgVDnWLpXrpdAZN20jwHyqTzKoVUvGlpfHEVwUhlfaXeR9OTaWXp3cuViS46mR0j_V1l0HicHbkfFJuBsM6QYsN6ohxwT0ZqVW69x4Pe_M-88NMyGFBDemE_PlewhllymqXEx3N4kpsLFmMAwzmbuG3sXYxYceHYo21VunkR-j1rq4OTZdH8h4-e3ArcqXA6VfEpmkvbG-XtRBTgtFx4mznqJpKNFbuyhytbr9L3GaD1-LVhtFaIX6v1zg20KWadvSwPxPAxP4koSMWfgq4_q0JOhtBFXF-ih4jF_iKvLPaQeMWO5YiDSpZiIBdW9ZCwhGM223vMEnkdmY94HskmQyMCR7GzLbTaF94pGwlVSqIyWEgWc2A-prFgafH9k56viQQ2-iFCZV5G6yQ_jOM7lohDZA3fhZCaAig0VQruprbBVNpxZBla0ri9IlyywHu7PCKH8gbdW51C0HTbNNN4BTdxI44eRK5sq54V2gA7XqUkPgOZnXKHi_blNjriTehE9LmGZmMlX_WubXq-JtGFmaettB4pwLpmuQXSPFqW_Gt8E7su2A"
}

try:
    print("--- Executing Request ---")
    response = requests.post(url, headers=headers, json=data, timeout=30)
    print(f"Status: {response.status_code}")
    print("Response Body:")
    try:
        print(json.dumps(response.json(), indent=2))
    except:
        print(response.text)
except Exception as e:
    print(f"Error: {e}")
