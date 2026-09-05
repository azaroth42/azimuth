"""Vehicles: things you ride in or on, and drive through exits.

Two kinds, and the difference is which mixin supplies the riding:

* ``Positionable`` + ``Vehicle`` -- a bicycle you sit **on**.  The rider keeps
  their own location, so the vehicle carries them along, re-seating them at
  the far end (``move_to`` drops a position, which is the trap these pin).
* ``Enterable`` + ``Vehicle`` -- a car you sit **in**.  The rider stands in
  the car's own interior Place, which travels with the car for free: their
  location never changes at all.

Neither needs a hand-written class -- they are composed from mixins at
runtime (see classfactory.py) -- so these also pin that the composed names
round-trip.
"""

from azimuth.entities import Exit, OpenableExit, Place
from azimuth.mixins import VEHICLE_LARGE, VEHICLE_NONE, Vehicle

from .framework import AzimuthTest

BIKE = "PositionableVehicleObject"
CAR = "EnterableVehicleObject"


class VehicleTest(AzimuthTest):
    """A driveway with three ways out: an open road, an ordinary front door,
    and a garage door -- the same class as the front door, just wide enough
    for a car."""

    def yard(self):
        w = self.tw.world
        self.drive_way = Place(None, w, {"name": "Driveway", "description": "Gravel."})
        self.road = Place(None, w, {"name": "The Road", "description": "Tarmac."})
        self.hall = Place(None, w, {"name": "Front Hall", "description": "A hall."})
        self.garage = Place(None, w, {"name": "Garage", "description": "Oil stains."})
        Exit(None, w, {"name": "north", "source": self.drive_way.id,
                       "destination": self.road.id})
        Exit(None, w, {"name": "south", "source": self.road.id,
                       "destination": self.drive_way.id})
        OpenableExit(None, w, {"name": "front door", "source": self.drive_way.id,
                               "destination": self.hall.id, "open": True})
        OpenableExit(None, w, {"name": "garage door", "source": self.drive_way.id,
                               "destination": self.garage.id, "open": True,
                               "max_vehicle_size": VEHICLE_LARGE})
        wiz = self.wizard()
        wiz.player.move_to(self.drive_way)
        self.tw.world.flush_state()
        return wiz

    def bike(self, **data):
        w = self.tw.world
        return w.compose(BIKE)(None, w, dict(
            {"name": "red bicycle", "aliases": ["bike"],
             "description": "A red bicycle.",
             "location": self.drive_way.id}, **data))

    def car(self, **data):
        w = self.tw.world
        return w.compose(CAR)(None, w, dict(
            {"name": "blue car", "aliases": ["car"], "description": "A blue car.",
             "vehicle_size": VEHICLE_LARGE,
             "location": self.drive_way.id}, **data))

    # -- composition -------------------------------------------------------

    def test_vehicles_need_no_handwritten_class(self):
        self.yard()
        for name, mixins in ((BIKE, ("Positionable", "Vehicle")),
                             (CAR, ("Enterable", "Vehicle"))):
            cls = self.tw.world.compose(name)
            assert cls.__name__ == name
            assert issubclass(cls, Vehicle)
            assert cls.__dict__["_az_mixins"] == mixins

    def test_composed_name_round_trips(self):
        """The generated name is the vehicle's identity: it has to parse back
        into base + mixins, or @create and a hand-written db record can't name
        one."""
        self.yard()
        f = self.tw.world.classes
        assert f.split_name(BIKE) == ("Object", ("Positionable", "Vehicle"))
        assert f.split_name(CAR) == ("Object", ("Enterable", "Vehicle"))

    def test_persists_and_reloads(self):
        """Cold reload: a fresh world off the same storage, so the car and its
        interior have to find each other from ids alone -- whichever the
        database happens to hand back first."""
        from azimuth.world import setup_world

        from .framework import WORLD_ID

        self.yard()
        w = self.tw.world
        car = self.car()
        interior_id = car.interior.id
        data = car.to_dict()
        assert data["class"] == "Object" and data["mixins"] == ["Enterable", "Vehicle"]
        assert data["interior"] == interior_id and data["vehicle_size"] == VEHICLE_LARGE
        w.dump_database()

        cold = setup_world(self.tw.storage, WORLD_ID)
        again = cold.get_object(car.id)
        assert type(again).__name__ == CAR
        assert again.vehicle_size == VEHICLE_LARGE
        assert again.interior.id == interior_id
        assert again.interior.outside is again, "the interior lost its car"

    def test_interior_survives_recomposition(self):
        """@addmixin rebuilds the instance; the interior must be reseated onto
        the new one, or the passengers can never get out."""
        wiz = self.yard()
        car = self.car()
        interior = car.interior
        wiz.send("get in car")
        self.assert_msg(wiz.send("@addmixin Openable to blue car"), "also Openable")
        rebuilt = self.tw.world.get_object(car.id)
        assert rebuilt is not car
        assert rebuilt.interior is interior
        assert interior.outside is rebuilt, "the interior still points at the old car"
        self.assert_msg(wiz.send("out"), "You get out of blue car")
        assert wiz.player.location is self.drive_way

    # -- not luggage -------------------------------------------------------

    def test_cannot_be_picked_up(self):
        wiz = self.yard()
        self.bike()
        self.assert_msg(wiz.send("get bike"), "not something you can carry")
        assert self.tw.world.get_object_by_name("red bicycle").location is self.drive_way

    # -- the bicycle: you sit on it, and it carries you --------------------

    def test_riding_carries_the_rider(self):
        wiz = self.yard()
        bike = self.bike()
        self.assert_msg(wiz.send("sit on bike"), "You sit on red bicycle")
        assert bike.is_aboard(wiz.player)
        self.assert_msg(wiz.send("drive north"), "You drive red bicycle through north")
        assert bike.location is self.road, "the bicycle should have moved"
        assert wiz.player.location is self.road, "the rider should have come along"

    def test_rider_is_still_seated_after_the_trip(self):
        """move_to drops a position, so the seating has to be taken down and
        put back around the move -- otherwise you arrive standing beside it."""
        wiz = self.yard()
        bike = self.bike()
        wiz.send("sit on bike")
        wiz.send("drive north")
        position = wiz.player.find_position()
        assert position is not None, "the rider fell off in transit"
        assert position[0] is bike and position[1] == "on"
        assert bike.is_aboard(wiz.player)

    def test_a_bare_direction_rides(self):
        """You don't step off the bicycle to walk north."""
        wiz = self.yard()
        bike = self.bike()
        wiz.send("sit on bike")
        self.assert_msg(wiz.send("north"), "You drive red bicycle through north")
        assert bike.location is self.road

    def test_cannot_drive_what_you_are_not_on(self):
        wiz = self.yard()
        bike = self.bike()
        self.assert_msg(wiz.send("drive north"), "You need to be aboard")
        assert bike.location is self.drive_way

    def test_dismount(self):
        wiz = self.yard()
        bike = self.bike()
        wiz.send("sit on bike")
        self.assert_msg(wiz.send("dismount"), "You get off red bicycle")
        assert wiz.player.find_position() is None
        assert not bike.is_aboard(wiz.player)

    # -- the car: you sit in it, and its interior travels with it ----------

    def test_getting_in_puts_you_in_the_car_not_the_room(self):
        wiz = self.yard()
        car = self.car()
        self.assert_msg(wiz.send("get in car"), "You get into blue car")
        assert wiz.player.location is car.interior
        assert wiz.player not in self.drive_way.contents
        assert car.is_aboard(wiz.player)

    def test_driving_moves_the_car_and_not_the_passenger(self):
        """The whole point of the interior: the passenger's location never
        changes, so nothing has to be carried."""
        wiz = self.yard()
        car = self.car()
        wiz.send("get in car")
        inside = wiz.player.location
        self.assert_msg(wiz.send("drive north"), "You drive blue car through north")
        assert car.location is self.road
        assert wiz.player.location is inside, "the passenger should not have moved"
        assert car.interior.location is None

    def test_passenger_is_shown_the_new_room(self):
        wiz = self.yard()
        self.car()
        wiz.send("get in car")
        self.assert_msg(wiz.send("drive north"), "The Road", "Tarmac.")

    def test_getting_out_lands_you_where_the_car_is(self):
        wiz = self.yard()
        car = self.car()
        wiz.send("get in car")
        wiz.send("drive north")
        self.assert_msg(wiz.send("out"), "You get out of blue car")
        assert wiz.player.location is self.road
        assert wiz.player in self.road.contents
        assert not car.is_aboard(wiz.player)

    def test_interior_says_what_is_outside(self):
        wiz = self.yard()
        self.car()
        wiz.send("get in car")
        self.assert_msg(wiz.send("look"), "Outside you can see Driveway")

    def test_looking_at_the_car_shows_who_is_in_it(self):
        wiz = self.yard()
        other = self.tw.register("passenger", "pw123456")
        other.player.move_to(self.drive_way)
        self.car()
        other.send("get in car")
        self.assert_msg(wiz.send("look at car"), "Inside is passenger")

    # -- exits that refuse -------------------------------------------------

    def test_a_car_will_not_fit_through_a_front_door(self):
        wiz = self.yard()
        car = self.car()
        wiz.send("get in car")
        self.assert_msg(wiz.send("drive front door"), "will not fit through front door")
        assert car.location is self.drive_way

    def test_a_garage_door_lets_it_through(self):
        """Same class as the front door -- only max_vehicle_size differs."""
        wiz = self.yard()
        car = self.car()
        wiz.send("get in car")
        self.assert_msg(wiz.send("drive garage door"), "You drive blue car through")
        assert car.location is self.garage

    def test_a_bicycle_fits_through_a_door(self):
        wiz = self.yard()
        bike = self.bike()
        wiz.send("sit on bike")
        wiz.send("drive front door")
        assert bike.location is self.hall

    def test_an_exit_can_bar_vehicles_outright(self):
        wiz = self.yard()
        self.drive_way.exits["north"].max_vehicle_size = VEHICLE_NONE
        bike = self.bike()
        wiz.send("sit on bike")
        self.assert_msg(wiz.send("drive north"), "will not fit through north")
        assert bike.location is self.drive_way

    def test_a_closed_door_stops_a_vehicle(self):
        wiz = self.yard()
        self.drive_way.exits["garage door"].is_open = False
        car = self.car()
        wiz.send("get in car")
        self.assert_msg(wiz.send("drive garage door"), "garage door is closed")
        assert car.location is self.drive_way

    def test_walking_is_unaffected(self):
        """Vehicle sizing must not change who may walk where."""
        wiz = self.yard()
        self.assert_msg(wiz.send("go front door"), "You go through")
        assert wiz.player.location is self.hall

    # -- seeing and steering from inside -----------------------------------

    def test_you_can_see_out_of_the_car(self):
        """A car has windows: the street, what is in it, and the ways out of
        it are all visible -- otherwise a driver cannot even look at the car
        they are sitting in."""
        wiz = self.yard()
        car = self.car()
        bike = self.bike()
        wiz.send("get in car")
        assert wiz.player.can_see(car)
        assert wiz.player.can_see(bike)
        assert wiz.player.can_see(self.drive_way.exits["north"])
        self.assert_msg(wiz.send("look at bike"), "A red bicycle.")

    def test_interior_lists_the_ways_out(self):
        wiz = self.yard()
        self.car()
        wiz.send("get in car")
        self.assert_msg(wiz.send("look"), "Outside you can see Driveway",
                        "Ways out:", "garage door")

    def test_going_through_an_exit_drives(self):
        """`go north` -- and a click on the exit in a client's panel, which
        sends exactly that -- means drive when you are the one at the wheel."""
        wiz = self.yard()
        car = self.car()
        wiz.send("get in car")
        self.assert_msg(wiz.send("go north"), "You drive blue car through north")
        assert car.location is self.road
        assert wiz.player.location is car.interior

    def test_the_state_channel_offers_those_exits(self):
        """The OOB room section has to report the vehicle's ways out, or a
        driver's panel is empty and there is nothing to click."""
        wiz = self.yard()
        self.tw.oob(wiz)
        self.car()
        wiz.send("get in car")
        self.tw.world.push_init(wiz.player)
        room = wiz.states()[-1]["room"]
        assert room["name"] == "inside blue car"
        assert {e["name"] for e in room["exits"]} == {
            "north", "front door", "garage door"
        }, room["exits"]

    def test_a_rider_on_a_bike_rides_through_an_exit(self):
        wiz = self.yard()
        bike = self.bike()
        wiz.send("sit on bike")
        self.assert_msg(wiz.send("go north"), "You drive red bicycle through north")
        assert bike.location is self.road
        assert wiz.player.find_position()[0] is bike

    def test_walking_off_requires_getting_off(self):
        """Nothing stops you leaving the bicycle behind -- you just have to
        say so."""
        wiz = self.yard()
        bike = self.bike()
        wiz.send("sit on bike")
        wiz.send("dismount")
        self.assert_msg(wiz.send("go north"), "You go through")
        assert wiz.player.location is self.road
        assert bike.location is self.drive_way

    # -- addressing the right vehicle --------------------------------------

    def test_drive_reaches_your_own_vehicle(self):
        """Two vehicles in the room: the command has to reach the one you are
        actually aboard, whichever the dispatcher would otherwise find."""
        wiz = self.yard()
        bike = self.bike()
        car = self.car()
        wiz.send("get in car")
        wiz.send("drive north")
        assert car.location is self.road, "the car should have moved"
        assert bike.location is self.drive_way, "the bicycle should not have"
