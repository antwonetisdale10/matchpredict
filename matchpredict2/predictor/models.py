from django.conf import settings
from django.db import models
from django.utils import timezone


class Season(models.Model):
    start_year = models.IntegerField(unique=True)
    # True once real results for the whole season are in (used only for labelling).
    is_complete = models.BooleanField(default=False)

    class Meta:
        ordering = ["start_year"]

    @property
    def label(self):
        return f"{self.start_year}/{str(self.start_year + 1)[-2:]}"

    def __str__(self):
        return self.label


class Team(models.Model):
    name = models.CharField(max_length=60, unique=True)  # football-data spelling
    short = models.CharField(max_length=4, blank=True)

    class Meta:
        ordering = ["name"]

    @property
    def slug(self):
        return self.name.lower().replace("'", "").replace(" ", "-")

    def __str__(self):
        return self.name


class Player(models.Model):
    """Squad member for a season (identity + LAST season's numbers only)."""

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="players")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    name = models.CharField(max_length=80)
    full_name = models.CharField(max_length=160, blank=True)
    pos = models.CharField(max_length=3)  # GK DEF MID FWD
    start_price = models.FloatField(null=True, blank=True)
    goals_prev = models.FloatField(null=True, blank=True)
    assists_prev = models.FloatField(null=True, blank=True)
    minutes_prev = models.FloatField(null=True, blank=True)
    pen_order = models.FloatField(null=True, blank=True)
    fk_order = models.FloatField(null=True, blank=True)
    yellows_prev = models.FloatField(null=True, blank=True)
    reds_prev = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["team__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.team})"


class Match(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="matches")
    matchweek = models.IntegerField()
    kickoff = models.DateTimeField()
    home = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="home_matches")
    away = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="away_matches")
    referee = models.CharField(max_length=60, blank=True)

    # Actual result. NULL = not played / not recorded. The prediction engine never reads
    # these for the season it is predicting (see engine.data.training_frame).
    home_goals = models.IntegerField(null=True, blank=True)
    away_goals = models.IntegerField(null=True, blank=True)
    ht_home = models.IntegerField(null=True, blank=True)
    ht_away = models.IntegerField(null=True, blank=True)
    home_shots = models.IntegerField(null=True, blank=True)
    away_shots = models.IntegerField(null=True, blank=True)
    home_sot = models.IntegerField(null=True, blank=True)
    away_sot = models.IntegerField(null=True, blank=True)
    home_fouls = models.IntegerField(null=True, blank=True)
    away_fouls = models.IntegerField(null=True, blank=True)
    home_corners = models.IntegerField(null=True, blank=True)
    away_corners = models.IntegerField(null=True, blank=True)
    home_yellow = models.IntegerField(null=True, blank=True)
    away_yellow = models.IntegerField(null=True, blank=True)
    home_red = models.IntegerField(null=True, blank=True)
    away_red = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["kickoff", "id"]
        indexes = [models.Index(fields=["season", "matchweek"])]
        constraints = [models.UniqueConstraint(fields=["season", "home", "away"], name="one_fixture_per_pairing")]

    @property
    def played(self):
        return self.home_goals is not None

    @property
    def locked(self):
        """User predictions close at kick-off."""
        return self.kickoff <= timezone.now()

    @property
    def result_letter(self):
        if not self.played:
            return None
        return "H" if self.home_goals > self.away_goals else "A" if self.home_goals < self.away_goals else "D"

    def __str__(self):
        return f"{self.home} v {self.away} ({self.season})"


class MatchEvent(models.Model):
    """Optional real events (staff-entered) so scorer / minute bonus picks can be settled."""

    GOAL, YELLOW, RED = "goal", "yellow", "red"
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="events")
    minute = models.IntegerField()
    kind = models.CharField(max_length=8)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    player = models.CharField(max_length=80, blank=True)
    assist = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["minute", "id"]


class Prediction(models.Model):
    """The model's frozen forecast for a match. Built from data BEFORE the season only."""

    match = models.OneToOneField(Match, on_delete=models.CASCADE, related_name="prediction")
    model_version = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    xg_home = models.FloatField()
    xg_away = models.FloatField()
    p_home = models.FloatField()
    p_draw = models.FloatField()
    p_away = models.FloatField()
    pred_home = models.IntegerField()
    pred_away = models.IntegerField()
    p_over25 = models.FloatField()
    p_btts = models.FloatField()
    p_home_cs = models.FloatField()
    p_away_cs = models.FloatField()
    exp_yellow_home = models.FloatField()
    exp_yellow_away = models.FloatField()
    p_red = models.FloatField()
    exp_corners = models.FloatField()
    p_penalty = models.FloatField()
    # scoreline grid, timeline, scorers, cards, stats, half-time/full-time ... (see engine.simulate)
    detail = models.JSONField(default=dict)

    @property
    def pick(self):
        return "H" if self.p_home >= max(self.p_draw, self.p_away) else "A" if self.p_away >= self.p_draw else "D"

    @property
    def confidence(self):
        return max(self.p_home, self.p_draw, self.p_away)

    def __str__(self):
        return f"{self.match}: {self.pred_home}-{self.pred_away}"


class TeamRating(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="ratings")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="ratings")
    attack = models.FloatField()
    defence = models.FloatField()  # higher = harder to score against
    overall = models.FloatField()
    history_weight = models.FloatField(default=0)  # how much real data backs this rating
    newcomer = models.BooleanField(default=False)
    detail = models.JSONField(default=dict)

    class Meta:
        unique_together = ("season", "team")


class TeamProjection(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="projections")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="projections")
    exp_points = models.FloatField()
    exp_gf = models.FloatField()
    exp_ga = models.FloatField()
    exp_rank = models.FloatField()
    p_title = models.FloatField()
    p_top4 = models.FloatField()
    p_relegation = models.FloatField()
    rank_dist = models.JSONField(default=list)

    class Meta:
        unique_together = ("season", "team")
        ordering = ["-exp_points"]


class BacktestResult(models.Model):
    """Walk-forward accuracy: predict each past season using only earlier seasons."""

    season = models.OneToOneField(Season, on_delete=models.CASCADE, related_name="backtest")
    model_version = models.CharField(max_length=200)
    n_matches = models.IntegerField()
    metrics = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class UserPrediction(models.Model):
    WINDOWS = [("1-15", "1–15′"), ("16-30", "16–30′"), ("31-45", "31–45′"), ("46-60", "46–60′"),
               ("61-75", "61–75′"), ("76-90", "76–90′"), ("none", "No goal")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="picks")
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="user_picks")
    home_goals = models.IntegerField()
    away_goals = models.IntegerField()
    ht_result = models.CharField(max_length=1, blank=True)  # H / D / A
    first_scorer = models.CharField(max_length=80, blank=True)
    first_goal_window = models.CharField(max_length=8, blank=True, choices=WINDOWS)
    red_card = models.BooleanField(null=True, blank=True)
    yellows_over = models.BooleanField(null=True, blank=True)  # total yellows over 3.5
    corners_over = models.BooleanField(null=True, blank=True)  # total corners over 9.5
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    settled = models.BooleanField(default=False)
    points = models.IntegerField(default=0)
    breakdown = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("user", "match")
        ordering = ["-match__kickoff"]

    @property
    def result_letter(self):
        return "H" if self.home_goals > self.away_goals else "A" if self.home_goals < self.away_goals else "D"


class UserBadge(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="badges")
    code = models.CharField(max_length=30)
    awarded_at = models.DateTimeField(auto_now_add=True)
    match = models.ForeignKey(Match, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        unique_together = ("user", "code")
