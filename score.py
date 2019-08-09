from collections import defaultdict
from enum import Enum
from enum import auto
import time
import sys
import tkinter as tk
import tkinter.font

class Team(Enum):
    UNKNOWN = auto()
    A = auto()
    B = auto()

class Tag(object):
    def __hash__(self):
        return hash(self.rfid)
    def __eq__(self, rhs):
        return isinstance(rhs, Tag) and self.rfid == rhs.rfid

    def __init__(self, rfid, team):
        # RFID tag (a String)
        self.rfid = rfid

        # A Team Enum
        self.team = team

class GameEvent(Enum):
    TAGS_CHANGED_ON_BOARD_1 = auto()
    TAGS_CHANGED_ON_BOARD_2 = auto()
    NO_CHANGE = auto()
    BUTTON_PRESSED = auto()
    TIMEOUT = auto()
    QUIT = auto()

class GameState(Enum):
    START = auto()
    PLAYING_BOARD_1 = auto()
    PLAYING_BOARD_2 = auto()
    TURN_OVER = auto()
    BOARD_CLEAR = auto()
    GAME_OVER = auto()

class Location(Enum):
    UNKNOWN = auto()
    BOARD_1 = auto()
    HOLE_1 = auto()
    BOARD_2 = auto()
    HOLE_2 = auto()

##########################################
#                 Sensor                 #
##########################################

# A Sensor that can read RFID tags.
class Sensor(object):
    # Check if the given tag was read in the most recent reading.
    # Do not perform a new reading.
    def was_tag_read(self, tag):
        return self.latest_reading.count(tag.rfid) > 0

    def read(self):
        self.latest_reading = self.read_tag_ids(self.id)
        return self.latest_reading

    def __init__(self, sensor_id, weight, location, read_tags, points):
        # Sensor's identifier (string)
        self.id = sensor_id

        # How much this sensor contributes to determining if the bean bag is on the board / in the hole.
        # If there is only one sensor and there are no false positives, this should be 1.
        self.weight = weight

        # Which board is this sensor installed on? (Location enum)
        self.location = location

        # Callback to scan for RFID tags within range
        #   Expected signature: lambda sensor_id: [string_id1,string_id2,...]
        self.read_tag_ids = read_tags

        # Results of latest call to read_tag_ids (List of string ids of Tags)
        self.latest_reading = []

        # If this sensor trips, how many points should be awarded? 
        # The maximum (not sum) of all possible points from all tripped sensors is used.
        # Typically 1 for board sensor, 3 for hole sensor.
        self.points_to_award = points

##########################################
#                  Game                  #
##########################################

# Play a game of Corn Hole
#
#   Three numbers are used for the score:
#     - Game score is the total points for a player from all turns excluding the current turn
#     - Turn score is the total points for a player from the current turn without subtracting the opponents score
#     - Total score is the Game Score plus the larger of zero and the difference between that team's Turn score and the opponent's Turn score.
class Game(object):

    def new_game(self):
        self.team_a_game_score = 0
        self.team_b_game_score = 0
        self.team_a_turn_score = 0
        self.team_b_turn_score = 0
        self.last_board_played = 0
        self.turn_number = 1
        # Time (in seconds since the epoch) when the turn started.
        # Ignore a button press is the turn has not lasted long enough, in case the player held the button down a long time 
        # and it triggered multiple times.
        self.time_of_turn_start = time.time()

    # End the turn, add the turn score to the game score, and zero out the turn scores.
    #   Only the team with the higher score for the turn sees their score increase.
    #   Only the difference between the turn scores for each team is added to the game score of the team that won the turn.
    #   Does not update the display.
    def end_turn(self):
        score_diff = self.team_a_turn_score - self.team_b_turn_score
        if score_diff > 0:
            self.team_a_game_score += score_diff
        else:
            self.team_b_game_score += abs(score_diff)
        self.team_a_turn_score = 0
        self.team_b_turn_score = 0
        self.turn_number += 1
        self.time_of_turn_start = time.time()

    ##########################################
    #        Filtering Sensors & Tags        #
    ##########################################

    def get_sensors_for_location(self, location):
        return list(filter(lambda sensor: (sensor.location == location), self.sensors))

    def get_tags_for_team(self, team):
        return list(filter(lambda tag: (tag.team == team), self.tags))

    # Get the Tag that has the given rfid, or None
    def get_tag_by_id(self, rfid):
        return (list(filter(lambda tag: (tag.rfid == rfid), self.tags)) or [None])[0]

    ##########################################
    #       Computing and Checking Scores    #
    ##########################################

    # Check the most recent sensor readings for all sensors at a given location and 
    # count how many points there are for the given team for that turn.
    # This does not perform a new sensor reading, but relies on the previous reading.
    # The same tag may register on more than one sensor. Only count it once!
    # However, each sensor increases the likelihood that the tag will count toward the score.
    # (It is possible for a tag to be ignored if it is deemed a false positive due to not being picked up by enough sensors.)
    def tally_location(self, team, location):
        tags_for_team = set(self.get_tags_for_team(team))
        max_score_per_tag = defaultdict(lambda : 0)
        hits_per_tag = defaultdict(lambda : 0)
        verified_tags = set()
        for sensor in self.get_sensors_for_location(location):
            for rfid in sensor.latest_reading:
                tag = self.get_tag_by_id(rfid)
                if tag is not None and (tag in tags_for_team):
                    max_score_per_tag[tag] = max(max_score_per_tag[tag], sensor.points_to_award)
                    hits_per_tag[tag] += sensor.weight
                    if hits_per_tag[tag] >= self.minimum_sensor_weight:
                        verified_tags.add(tag)
        tally = 0
        for verified_tag in verified_tags:
            tally += max_score_per_tag[verified_tag]
        return tally
                
    # Compute current turn score for given team for board 1, but do not update display or game variables
    def tally_turn_board_1(self, team):
        on_board = self.tally_location(team, Location.BOARD_1)
        in_hole = self.tally_location(team, Location.HOLE_1)
        return on_board + in_hole

    # Compute current turn score for given team for board 2, but do not update display or game variables
    def tally_turn_board_2(self, team):
        on_board = self.tally_location(team, Location.BOARD_2)
        in_hole = self.tally_location(team, Location.HOLE_2)
        return on_board + in_hole

    # Update game variables with current turn scores for both teams. Does not update display.
    def update_turn_score(self, board_number):
        if board_number == 1:
            self.team_a_turn_score = self.tally_turn_board_1(Team.A)
            self.team_b_turn_score = self.tally_turn_board_1(Team.B)
            self.last_board_played = 1
        else:
            self.team_a_turn_score = self.tally_turn_board_2(Team.A)
            self.team_b_turn_score = self.tally_turn_board_2(Team.B)
            self.last_board_played = 2

    def are_boards_clear(self):
        return 0 == self.tally_turn_board_1(Team.A) + self.tally_turn_board_1(Team.B)

    def total_score(self, team):
        if team == Team.A:
            return self.team_a_game_score + max(0, self.team_a_turn_score - self.team_b_turn_score)
        else:
            return self.team_b_game_score + max(0, self.team_b_turn_score - self.team_a_turn_score)

    def have_reached_winning_score(self):
        return self.total_score(Team.A) >= 21 or self.total_score(Team.B) >= 21


    ##########################################
    #            Timing Related              #
    ##########################################

    # Get the smallest of the several time periods that are important to the game, to use as the time 
    # to sleep between checking the sensors and buttons.
    def poll_interval_in_seconds(self):
        return min(self.rfid_polling_interval_in_seconds, self.button_polling_interval_in_seconds, self.timeout_in_seconds)

    def current_turn_duration_in_seconds(self):
        return time.time() - self.time_of_turn_start

    ##########################################
    #          I/O Device Interfaces         #
    ##########################################

    def read_buttons(self):
        self.time_of_last_button_read = time.time()
        return self.get_button_state()

    def read_sensors(self):
        for sensor in self.sensors:
            sensor.read()
        self.time_of_last_sensor_read = time.time()

    # Display the scores plus the optional message
    def display(self, message):
        return self.display_scores(message, 
            self.total_score(Team.A), self.team_a_game_score, self.team_a_turn_score, 
            self.total_score(Team.B), self.team_b_game_score, self.team_b_turn_score)

    ##########################################
    #            Event Reader                #
    ##########################################

    # Get the next event, which may involve checking the button state, reading the sensors, or triggering a timeout.
    # Abide by the timers, which indicate whether it is time to perform a given sensor check or trigger a timeout.
    # For certain events, reset the relevant timers.
    # There is a precedence: 
    #    - first check for a change in score due to tags being added or removed
    #    - next check for a button click
    #    - last check for a timeout
    # Do not cause any of the side effects of an event other than adjusting timers and setting last_board_played. 
    def get_event(self):
        now = time.time()

        # Possibly check for a button press.
        # If the current turn has not been going on long enough, ignore the buttons;
        # the player was holding don the button too long or clicked it multiple times in succession.
        if self.current_turn_duration_in_seconds() > 4 and now - self.time_of_last_button_read >= self.button_polling_interval_in_seconds:
            button_state = self.read_buttons()
            # If the button was pressed on either Board 1 or Board 2, we do the same thing.
            if button_state[0] or button_state[1]:
                return GameEvent.BUTTON_PRESSED
            if button_state[2]:
                return GameEvent.QUIT
        # Check for potential changes in the turn score due to tags being added or removed.
        if now - self.time_of_last_sensor_read >= self.rfid_polling_interval_in_seconds:
            self.read_sensors()
            a1 = self.tally_turn_board_1(Team.A)
            b1 = self.tally_turn_board_1(Team.B)
            a2 = self.tally_turn_board_2(Team.A)
            b2 = self.tally_turn_board_2(Team.B)

            if a1+b1 > 0:
                # Is Activity definitely on Board 1?
                if a1 != self.team_a_turn_score or b1 != self.team_b_turn_score:
                    self.last_board_played = 1
                    return GameEvent.TAGS_CHANGED_ON_BOARD_1
            elif a2+b2 > 0:
                # Is Activity definitely on Board 2?
                if a2 != self.team_a_turn_score or b2 != self.team_b_turn_score:
                    self.last_board_played = 2
                    return GameEvent.TAGS_CHANGED_ON_BOARD_2
            elif self.team_a_turn_score + self.team_b_turn_score > 0:
                # Combined turn Score was previously positive, but dropped to zero,
                # so assume that play occurred on the same board as last time.
                if self.last_board_played == 1:
                    return GameEvent.TAGS_CHANGED_ON_BOARD_1
                else:
                    return GameEvent.TAGS_CHANGED_ON_BOARD_2
        # No button pressed and no change to tags read.
        # Check for a timeout.
        if self.current_turn_duration_in_seconds() >= self.timeout_in_seconds:
            return GameEvent.TIMEOUT
        else:
            return GameEvent.NO_CHANGE

    ##########################################
    #   Event Handlers for each GameState    #
    ##########################################

    def process_start_state(self, event):
        if event == GameEvent.NO_CHANGE:
            return GameState.START
        elif event == GameEvent.TIMEOUT:
            return GameState.START
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_1:
            self.turn_number = 1
            self.update_turn_score(1)
            self.display('')
            return GameState.PLAYING_BOARD_1
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_2:
            self.turn_number = 1
            self.update_turn_score(2)
            self.display(f'Turn {self.turn_number}')
            return GameState.PLAYING_BOARD_2
        elif event == GameEvent.BUTTON_PRESSED:
            return GameState.START
        else:
            return GameState.START

    def process_playing_board_1_state(self, event):
        if self.have_reached_winning_score():
            self.display('Game Over')
            return GameState.GAME_OVER
        if event == GameEvent.NO_CHANGE:
            return GameState.PLAYING_BOARD_1
        elif event == GameEvent.TIMEOUT:
            # Timeout here ends the turn
            self.update_turn_score(1)
            self.end_turn()
            self.display(f'Turn {self.turn_number}')
            return GameState.TURN_OVER
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_1:
            self.update_turn_score(1)
            self.display(f'Turn {self.turn_number}')
            return GameState.PLAYING_BOARD_1
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_2:
            # Error condition - bags on two boards at once!
            return GameState.PLAYING_BOARD_1
        elif event == GameEvent.BUTTON_PRESSED:
            self.update_turn_score(1)
            self.end_turn()
            self.display(f'Turn {self.turn_number}')
            return GameState.TURN_OVER
        else:
            return GameState.PLAYING_BOARD_1

    def process_playing_board_2_state(self, event):
        if self.have_reached_winning_score():
            self.display('Game Over')
            return GameState.GAME_OVER
        if event == GameEvent.NO_CHANGE:
            return GameState.PLAYING_BOARD_2
        elif event == GameEvent.TIMEOUT:
            # Timeout here ends the turn
            self.update_turn_score(2)
            self.end_turn()
            self.display(f'Turn {self.turn_number}')
            return GameState.TURN_OVER
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_1:
            # Error condition - bags on two boards at once!
            return GameState.PLAYING_BOARD_2
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_2:
            self.update_turn_score(2)
            self.display(f'Turn {self.turn_number}')
            return GameState.PLAYING_BOARD_2
        elif event == GameEvent.BUTTON_PRESSED:
            self.update_turn_score(2)
            self.end_turn()
            self.display(f'Turn {self.turn_number}')
            return GameState.TURN_OVER
        else:
            return GameState.PLAYING_BOARD_2

    # Event handler for the TURN_OVER state.
    # A crucial feature of this state is that we cannot leave it until
    # the total number of Tags detected by the readers drops to zero.
    # This is the cleanup phase, when players are removing the bean bags from the board and hole.
    def process_turn_over_state(self, event):
        if self.are_boards_clear():
            return GameState.BOARD_CLEAR
        elif self.have_reached_winning_score():
            self.display('Game Over')
            return GameState.GAME_OVER
        elif event == GameEvent.TIMEOUT:
            return GameState.GAME_OVER
        elif event == GameEvent.BUTTON_PRESSED:
            return GameState.GAME_OVER
        else:
            return GameState.TURN_OVER

    # Event handler for the BOARD_CLEAR state.
    def process_board_clear_state(self, event):
        if self.have_reached_winning_score():
            self.display('Game Over')
            return GameState.GAME_OVER
        if event == GameEvent.NO_CHANGE:
            return GameState.BOARD_CLEAR
        elif event == GameEvent.TIMEOUT:
            return GameState.GAME_OVER
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_1:
            self.update_turn_score(1)
            self.display(f'Turn {self.turn_number}')
            return GameState.PLAYING_BOARD_1
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_2:
            self.update_turn_score(2)
            self.display(f'Turn {self.turn_number}')
            return GameState.PLAYING_BOARD_2
        elif event == GameEvent.BUTTON_PRESSED:
            return GameState.GAME_OVER
        else:
            return GameState.BOARD_CLEAR

    def process_playing_game_over_state(self, event):
        self.display('Game Over')
        if event == GameEvent.NO_CHANGE:
            return GameState.GAME_OVER
        elif event == GameEvent.TIMEOUT:
            self.new_game()
            self.display('New Game')
            return GameState.START
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_1:
            self.new_game()
            self.update_turn_score(1)
            self.display('New Game')
            return GameState.PLAYING_BOARD_1
        elif event == GameEvent.TAGS_CHANGED_ON_BOARD_2:
            self.new_game()
            self.update_turn_score(2)
            self.display('New Game')
            return GameState.PLAYING_BOARD_2
        elif event == GameEvent.BUTTON_PRESSED:
            self.new_game()
            self.display('New Game')
            return GameState.START
        else:
            return GameState.GAME_OVER

    # Begin event polling loop and play a game
    def play(self):
        print("Begin game\n")
        self.state = GameState.START
        state_machine = {
            GameState.START : lambda event : self.process_start_state(event),
            GameState.PLAYING_BOARD_1 : lambda event : self.process_playing_board_1_state(event),
            GameState.PLAYING_BOARD_2 : lambda event : self.process_playing_board_2_state(event),
            GameState.BOARD_CLEAR : lambda event : self.process_board_clear_state(event),
            GameState.TURN_OVER : lambda event : self.process_turn_over_state(event),
            GameState.GAME_OVER : lambda event : self.process_playing_game_over_state(event),
        }
        event = GameEvent.NO_CHANGE
        poll_interval = self.poll_interval_in_seconds()
        while event != GameEvent.QUIT:
            event = self.get_event()
            action = state_machine[self.state]
            previous_state = self.state
            self.state = action(event)
            transition_message = f'Event {event.name} transitions From {previous_state.name} To {self.state.name}'
            if previous_state != GameState.START or self.state != GameState.START or event != GameEvent.NO_CHANGE:
                self.log(transition_message)
            time.sleep(poll_interval)

    ##########################################
    #            Constructor                 #
    ##########################################


    # Construct a Game. 
    #    tags ........... holds all the tags for all the bean bags and identifies which bags go with which team.
    #    sensors ........ must hold the sensors that are equipped to poll the sensors for RFID tag hits
    #    button_state ... must check the current state of the end of turn button
    #    display ........ must be a lambda that can update the score board with the scores for the two teams and an optional message.
    def __init__(self, tags, sensors, button_state, display):
        self.new_game()

        # All the sensors for both boards (a list of Sensor)
        self.sensors = sensors

        # Game state for state machine
        self.state = GameState.START

        # All the RFID tags, matching each tag to a team (a List of Tag)
        self.tags = tags

        # Sum up the weights of all tripped sensors. If it exceeds or equals this, that bean bag counts.
        self.minimum_sensor_weight = 1

        # Callback that updates the digital display of the scores and returns the total, game and turn scores.
        # Expected signature: 
        #   lambda message, team_a_total, team_a_game, team_a_turn, team_b_total, team_b_game, team_b_turn : [team_a_total, team_a_game, team_a_turn, team_b_total, team_b_game, team_b_turn]
        self.display_scores = display

        # Callback that checks and possibly resets the state of the button.
        # First element is for Board 1, second for Board 2. Third element is true for a Quit signal.
        # Expected signature: 
        #   lambda : [Bool, Bool, Bool]
        self.get_button_state = button_state

        # How often to poll the sensors
        self.rfid_polling_interval_in_seconds = 2.5

        # How often to check whether the button has been pushed
        self.button_polling_interval_in_seconds = 0.25

        # A turn ends if this many seconds pass without a change in the number of bean bags is detected.
        # Or the game ends, if a turn ends and a second timeout occurs.
        self.timeout_in_seconds = 45

        # Last time (in seconds since the epoch) an event occurred, such as pressing the button 
        # or detecting a change in the turn score for either team.
        # This is used to decide if we need to issue a timeout or recheck the sensors.
        self.time_of_last_change = time.time()

        # Last time (in seconds since the epoch) that we checked the sensors.
        self.time_of_last_sensor_read = time.time()

        # Last time (in seconds since the epoch) that we checked the button state.
        self.time_of_last_button_read = time.time()

        self.log = lambda message : message

    def __str__(self):
        return f'Team A: {self.team_a_game_score}  Team B: {self.team_b_game_score}'

##########################################
#            CornHoleApp                 #
##########################################

class CornHoleApp:
    def __init__(self):   
        test_game = TestGame()
        self.game = test_game.game
        # Need to replace the drivers, sensors...

    def new_game(self):
        self.zero_scores()
        self.turn_number = 1
        self.redisplay_scores()

    def zero_scores(self):
        # A fixed, A turn, B fixed, B turn
        self.scores = [0,0,0,0]

    def change_turn_score(self, team_letter, delta):
        if team_letter == 'A':
            self.scores[1] = max(0, self.scores[1] + delta)
        else:
            self.scores[3] = max(0, self.scores[3] + delta)

    def change_turn_score_and_redisplay(self, team_letter, delta):
        self.change_turn_score(team_letter, delta)
        self.redisplay_scores()

    def end_turn(self):
        a_total_score = self.get_total_score('A')
        b_total_score = self.get_total_score('B')
        self.scores = [a_total_score,0,b_total_score,0]
        self.turn_number += 1
        self.redisplay_scores()
    
    def get_total_score(self, team_letter):
        if team_letter == 'A':
            return self.scores[0] + max(0, self.scores[1] - self.scores[3])
        else:
            return self.scores[2] + max(0, self.scores[3] - self.scores[1])

    def redisplay_scores(self):
        a_score = self.get_total_score("A")
        b_score = self.get_total_score("B")
        self.team_a_score_variable.set(f'{a_score}')
        self.team_b_score_variable.set(f'{b_score}')
        self.team_a_turn_score_variable.set(f'   {self.scores[1]}')
        self.team_b_turn_score_variable.set(f'   {self.scores[3]}')
        if a_score >= 21 and a_score > b_score:
            message = 'Home wins!'
        elif b_score >= 21 and b_score > a_score:
            message = 'Visitors win!'
        else:
            message = f'Turn {self.turn_number}'
        self.message_variable.set(message)

    def build(self):
        self.window = tk.Tk()

        # Dynamic variables that control the display
        self.team_a_score_variable = tk.StringVar()
        self.team_b_score_variable = tk.StringVar()
        self.team_a_turn_score_variable = tk.StringVar()
        self.team_b_turn_score_variable = tk.StringVar()
        self.message_variable = tk.StringVar()

        self.font = tkinter.font.Font(root = self.window, family = 'Helvetica', size = 18, weight = "bold")
        self.medium_font = tkinter.font.Font(root = self.window, family = 'Helvetica', size = 48, weight = "bold")
        self.big_font = tkinter.font.Font(root = self.window, family = 'Helvetica', size = 96, weight = "bold")
        self.window.title("Corn Hole")

        # To fix window size:
        # self.window.geometry("800x600")

        # To prevent resizing:
        # self.window.resizable(0, 0)

        # Team Name Labels
        self.team_a_label = tk.Label(self.window, text="Home  ", font = self.big_font)
        self.team_b_label = tk.Label(self.window, text="Visitors  ", font = self.big_font)

        # Score display
        self.new_game()
        self.team_a_score = tk.Label(self.window, textvariable=self.team_a_score_variable, font = self.big_font)
        self.team_b_score = tk.Label(self.window, textvariable=self.team_b_score_variable, font = self.big_font)
        self.team_a_turn_score = tk.Label(self.window, textvariable=self.team_a_turn_score_variable, font = self.big_font)
        self.team_b_turn_score = tk.Label(self.window, textvariable=self.team_b_turn_score_variable, font = self.big_font)
        self.message = tk.Label(self.window, textvariable=self.message_variable, font = self.medium_font)

        # Buttons
        self.a_plus_button = self.counter_button('+', '#cc4d12', lambda : self.change_turn_score_and_redisplay('A', 1))
        self.a_minus_button = self.counter_button('-', '#cc4d12', lambda : self.change_turn_score_and_redisplay('A', -1))
        self.b_plus_button = self.counter_button('+', '#cc4d12', lambda : self.change_turn_score_and_redisplay('B', 1))
        self.b_minus_button = self.counter_button('-', '#cc4d12', lambda : self.change_turn_score_and_redisplay('B', -1))
        self.new_turn_button = self.counter_button('>', '#ebc034', lambda : self.end_turn())


        self.team_a_label.grid(row = 0, column = 0, sticky = tk.NW)
        self.team_b_label.grid(row = 1, column = 0, sticky = tk.W)
        self.team_a_score.grid(row = 0, column = 1, sticky = tk.NE)
        self.team_b_score.grid(row = 1, column = 1, sticky = tk.E)
        self.team_a_turn_score.grid(row = 0, column = 2, sticky = tk.NE)
        self.team_b_turn_score.grid(row = 1, column = 2, sticky = tk.E)
        self.a_minus_button.grid(row = 0, column = 3, sticky = tk.NW)
        self.a_plus_button.grid(row = 0, column = 4, sticky = tk.NW)
        self.b_minus_button.grid(row = 1, column = 3, sticky = tk.E)
        self.b_plus_button.grid(row = 1, column = 4, sticky = tk.E)
        self.new_turn_button.grid(row = 2, column = 3, sticky = tk.E)
        self.message.grid(row = 2, column = 0, columnspan = 2, sticky = tk.S)

        return self
    
    def counter_button(self, label, color, action):
        return tk.Button(
            self.window, 
            text = label, 
            font = self.big_font, 
            command = action, 
            bg = color, 
            height = 1, 
            width = 1
        )

    def exit_program(self):
        self.window.quit()

    def run(self):
        tk.mainloop()

##########################################
#            Test Classes                #
##########################################


class TestName(Enum):
    TOP_1 = auto()
    HOLE_1 = auto()
    TOP_2 = auto()
    HOLE_2 = auto()

class TestGame(object):
    def read_tags(self, sensor_id):
        response = input(f'Tags read by {sensor_id} (A1-4, B1-4)? ')
        return [x.strip() for x in response.split(',')]
    def read_button_states(self):
        response1 = input(f'Button 1 Pressed (Y/N)? ')
        response2 = input(f'Button 2 Pressed (Y/N)? ')
        quit_signal = response1 == 'Q' or response2 == 'Q'
        return [response1 == 'Y', response2 == 'Y', quit_signal]
    def display_score(self, message, a_total, a_game, a_turn, b_total, b_game, b_turn):
        print(f'\n{message}\n  Team A: Total = {a_total}, Turn = {a_turn}\n  Team B: Total = {b_total}, Turn = {b_turn}\n')
        return [a_total, a_game, a_turn, b_total, b_game, b_turn]

    def __init__(self):
        tags = [
            Tag("A1", Team.A),
            Tag("A2", Team.A),
            Tag("A3", Team.A),
            Tag("A4", Team.A),
            Tag("B1", Team.B),
            Tag("B2", Team.B),
            Tag("B3", Team.B),
            Tag("B4", Team.B)
        ]
        sensors = [
            Sensor(TestName.TOP_1.name, 1, Location.BOARD_1, lambda sensor_id: self.read_tags(TestName.TOP_1.name), 1),
            Sensor(TestName.HOLE_1.name, 1, Location.BOARD_1, lambda sensor_id: self.read_tags(TestName.HOLE_1.name), 3),
            Sensor(TestName.TOP_2.name, 1, Location.BOARD_1, lambda sensor_id: self.read_tags(TestName.TOP_2.name), 1),
            Sensor(TestName.HOLE_2.name, 1, Location.BOARD_1, lambda sensor_id: self.read_tags(TestName.HOLE_2.name), 3),
        ]
        self.game = Game(tags, sensors, 
            lambda : self.read_button_states(), 
            lambda message, a_total, a_game, a_turn, b_total, b_game, b_turn : self.display_score(message, a_total, a_game, a_turn, b_total, b_game, b_turn))
        self.game.log = lambda message : print(f'LOG: {message}')
        self.game.timeout_in_seconds = 300


if len(sys.argv) > 1 and sys.argv[len(sys.argv) - 1] == "test":
    print('Command line Corn Hole tester\n')
    test_game = TestGame()
    test_game.game.play()
    print('Quitting\n')
else:
    app = CornHoleApp()
    app.build().run()

