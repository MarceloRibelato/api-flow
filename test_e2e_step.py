import asyncio
from app.services.playwright_executor_service import PlaywrightExecutorService

async def main():
    executor = PlaywrightExecutorService(headless=True)
    await executor.start()
    
    step_data = {
        'type': 'browser',
        'properties': {
            'value': 'https://example.com'
        }
    }
    
    res = await executor.execute_step(step_data)
    print("RES:", res)
    
    intercepted = executor.pop_captured_requests()
    print("INTERCEPTED:", len(intercepted))
    
    await executor.stop()

asyncio.run(main())
