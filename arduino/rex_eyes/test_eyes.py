import asyncio
from cantina_os.services.eye_light_controller_service import EyeLightControllerService
from cantina_os.services.eye_light_controller_service import EyePattern

async def test_led_controller():
    # Create service instance
    service = EyeLightControllerService(mock_mode=False)
    
    try:
        # Start the service
        print("Starting LED controller service...")
        await service.start()
        await asyncio.sleep(1)  # Wait for initialization
        
        # Test pattern sequence
        patterns = [
            (EyePattern.IDLE, None, 0.5),
            (EyePattern.HAPPY, "#00FF00", 0.8),
            (EyePattern.SPEAKING, "#0000FF", 1.0),
            (EyePattern.SAD, "#FF0000", 0.3),
            (EyePattern.IDLE, None, 0.5)
        ]
        
        for pattern, color, brightness in patterns:
            print(f"\nSetting pattern: {pattern}, color: {color}, brightness: {brightness}")
            success = await service.set_pattern(pattern, color=color, brightness=brightness)
            print(f"Command {'succeeded' if success else 'failed'}")
            await asyncio.sleep(2)  # Wait to see the pattern
            
    except Exception as e:
        print(f"Error during test: {e}")
    finally:
        # Clean up
        print("\nStopping service...")
        await service.stop()

if __name__ == "__main__":
    asyncio.run(test_led_controller()) 