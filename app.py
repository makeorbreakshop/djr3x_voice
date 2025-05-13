from services.debug import DebugService

class Application:
    def __init__(self):
        self.services = []
        
    async def initialize(self):
        # Initialize DebugService first
        debug_service = DebugService()
        await debug_service.initialize()
        self.services.append(debug_service)
        
        # Initialize other services
        # They will automatically have access to debug methods through BaseService
        # ... existing code ... 