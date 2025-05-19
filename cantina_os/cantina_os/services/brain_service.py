async def _handle_dj_mode_changed(self, payload: Dict[str, Any]) -> None:
        """Handle DJ mode activation/deactivation.
        
        Args:
            payload: Dict with dj_mode_active boolean
        """
        try:
            dj_mode_active = payload.get("dj_mode_active", False)
            
            if dj_mode_active:
                self.logger.info("DJ mode activated")
                self._dj_mode_active = True
                
                # Select initial track using smart track selection
                track_name = await self._smart_track_selection()
                if not track_name:
                    self.logger.warning("No tracks available for DJ mode")
                    return
                    
                # Add to recently played
                self._recently_played_tracks.append(track_name)
                if len(self._recently_played_tracks) > self._max_recent_tracks:
                    self._recently_played_tracks.pop(0)
                    
                # Emit DJ mode start event with initial track
                await self.emit(
                    EventTopics.DJ_MODE_START,
                    {
                        "track_name": track_name,
                        "dj_mode_active": True
                    }
                )
            else:
                self.logger.info("DJ mode deactivated")
                self._dj_mode_active = False
                self._recently_played_tracks.clear()
                
                # Emit DJ mode stop event
                await self.emit(
                    EventTopics.DJ_MODE_STOP,
                    {
                        "dj_mode_active": False
                    }
                )
                
        except Exception as e:
            self.logger.error(f"Error handling DJ mode change: {e}", exc_info=True)
            
    async def _smart_track_selection(self, query: str = None) -> Optional[str]:
        """Smart track selection for both voice commands and DJ mode.
        
        Args:
            query: Optional search query to filter tracks
            
        Returns:
            Selected track name or None if no tracks available
        """
        try:
            # Get all available tracks
            available_tracks = self._music_library.get_track_names()
            if not available_tracks:
                self.logger.warning("No tracks available in music library")
                return None
                
            # Filter out recently played tracks for DJ mode
            if self._dj_mode_active:
                available_tracks = [t for t in available_tracks if t not in self._recently_played_tracks]
                if not available_tracks:
                    # If all tracks have been played recently, clear history and try again
                    self.logger.info("All tracks played recently, resetting history")
                    self._recently_played_tracks.clear()
                    available_tracks = self._music_library.get_track_names()
                    
            # If we have a query, use it to filter tracks
            if query:
                query = query.lower()
                filtered_tracks = [t for t in available_tracks if query in t.lower()]
                if filtered_tracks:
                    available_tracks = filtered_tracks
                    
            # Select a random track from available ones
            import random
            selected_track = random.choice(available_tracks)
            
            return selected_track
            
        except Exception as e:
            self.logger.error(f"Error in smart track selection: {e}", exc_info=True)
            return None
            
    async def _handle_dj_next_track(self, payload: Dict[str, Any]) -> None:
        """Handle request for next track in DJ mode.
        
        Args:
            payload: Command payload (unused)
        """
        try:
            if not self._dj_mode_active:
                self.logger.warning("Received next track request but DJ mode is not active")
                return
                
            # Use smart track selection to get next track
            next_track = await self._smart_track_selection()
            if not next_track:
                self.logger.warning("No tracks available for next selection")
                return
                
            # Add to recently played
            self._recently_played_tracks.append(next_track)
            if len(self._recently_played_tracks) > self._max_recent_tracks:
                self._recently_played_tracks.pop(0)
                
            # Emit next track event
            await self.emit(
                EventTopics.DJ_NEXT_TRACK_SELECTED,
                {
                    "track_name": next_track
                }
            )
            
        except Exception as e:
            self.logger.error(f"Error handling next track request: {e}", exc_info=True) 