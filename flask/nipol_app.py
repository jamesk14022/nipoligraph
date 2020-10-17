from flask import Flask, render_template, url_for, request, flash, jsonify
import datetime, re, getpass
import pickle, json, feather
import pandas as pd
import altair
import numpy as np
from itertools import product
from collections import defaultdict
from sklearn.decomposition import PCA

app = Flask(__name__)
app.debug = False
app.secret_key = 'dont_need_one'

#Load data
#---------
#Files used:
#Rarely updated:
#- all_politicians_list.csv (names and ids)
#- party_group_dict.json (uni/nat labels)
#- party_names_translation_short.json
#- party_names_translation.json
#- mp_api_numbers.json
#- member_other_photo_links.json
#- party_colours.csv
#- mlas_2019_twitter_accounts_toJuly2020_incMPs.csv
#- contribs_lda_model.pkl
#- newsriver_articles_[may,june,july]2020.feather
#- election_details.csv
#- election_results.csv
#- poll_details.csv
#- poll_results.csv
#Regular update:
#- mlas_2019_tweets_apr2019min_to_present.feather
#- vader_scored_tweets_apr2019min_to_present.csv
#- niassembly_questions_alltopresent.feather
#- niassembly_answers_alltopresent.feather
#- division_votes.feather
#- division_vote_results.feather
#- plenary_hansard_contribs_201920sessions_topresent.feather
#- lda_scored_plenary_contribs.csv
#- diary_future_events.psv
#- current_ministers_and_speakers.csv
#- current_committee_memberships.csv
#- newsriver_articles_ongoing2020.feather
#- static/tweets_network_since1july2020_edges.csv
#- static/tweets_network_since1july2020_nodes.json

if getpass.getuser() == 'david':
    data_dir = '/home/david/projects/text-work/mla_tweets/data/'
    test_mode = True
else:
    data_dir = '/home/vivaronaldo/nipol/data/'
    test_mode = False

mla_ids = pd.read_csv(data_dir + 'all_politicians_list.csv', dtype = {'PersonId': object})
#If have too many non-MLA/MPs, could become unreliable over time, so limit to active here
mla_ids = mla_ids[mla_ids.active==1]

#Add email address
mla_ids = mla_ids.merge(
    pd.read_csv(data_dir + 'mla_email_addresses.csv', dtype = {'PersonId': object}),
    on = 'PersonId', how = 'left'
    )

with open(data_dir + 'party_group_dict.json', 'r') as f:
    party_group_dict = json.load(f)
#Do this before turning PartyName to short form
mla_ids['PartyGroup'] = mla_ids.PartyName.apply(lambda p: party_group_dict[p])

mla_minister_roles = pd.read_csv(data_dir + 'current_ministers_and_speakers.csv', dtype={'PersonId': object})
mla_minister_roles = mla_minister_roles.merge(mla_ids[['PersonId','normal_name']], on='PersonId', how='inner')
mla_minister_roles = {i[1]['normal_name']: i[1]['AffiliationTitle'] for i in mla_minister_roles.iterrows()}

committee_roles = pd.read_csv(data_dir + 'current_committee_memberships.csv', dtype={'PersonId': object})
committee_roles = committee_roles.merge(mla_ids[['PersonId','normal_name']], on='PersonId', how='inner')

#Handle the two forms of some party names
#with open(data_dir + 'party_names_translation.json', 'r') as f:
with open(data_dir + 'party_names_translation_short.json', 'r') as f:
    party_names_translation = json.load(f)
with open(data_dir + 'party_names_translation.json', 'r') as f:
    party_names_translation_long = json.load(f)
#Numbers to find MP portraits on parliament website
with open(data_dir + 'mp_api_numbers.json', 'r') as f:
    mp_api_numbers = json.load(f)
with open(data_dir + 'member_other_photo_links.json', 'r') as f:
    member_other_photo_links = json.load(f)

mla_ids['PartyName_long'] = mla_ids.PartyName.apply(lambda p: party_names_translation_long[p])
mla_ids['PartyName'] = mla_ids.PartyName.apply(lambda p: party_names_translation[p])

party_colours = pd.read_csv(data_dir + 'party_colours.csv')
col_corrects = {'yellow3':'gold', 'green2':'lawngreen'}
party_colours['colour'] = party_colours['colour'].apply(
    lambda c: c if c not in col_corrects.keys() else col_corrects[c]
)
#Twitter
#-------

tweets_df = feather.read_dataframe(data_dir + 'mlas_2019_tweets_apr2019min_to_present_slim.feather')
twitter_ids = pd.read_csv(data_dir + 'mlas_2019_twitter_accounts_toJuly2020_incMPs.csv',
    dtype = {'user_id': object})
tweets_df = tweets_df.merge(twitter_ids[['user_id','mla_party','mla_name']].rename(index=str, columns={'mla_name':'normal_name'}), 
    on='user_id', how='left')
tweets_df['mla_party'] = tweets_df.mla_party.apply(lambda p: party_names_translation[p])
#Filter to 1 July onwards - fair comparison for all
tweets_df = tweets_df[tweets_df.created_ym >= '202007']
tweets_df = tweets_df[tweets_df['normal_name'].isin(mla_ids['normal_name'])]
tweets_df['tweet_type'] = tweets_df.is_retweet.apply(lambda b: 'retweet' if b else 'original')
tweets_df['created_weeksfromJan2020'] = tweets_df['created_at'].dt.week
max_week_tweets = tweets_df['created_weeksfromJan2020'].max()
tweet_sentiment = pd.read_csv(data_dir + 'vader_scored_tweets_apr2019min_to_present.csv', dtype={'status_id': object})
tweets_df = tweets_df.merge(tweet_sentiment, on='status_id', how='left')

#Which people tweet the most
top_tweeters = tweets_df[tweets_df.created_weeksfromJan2020 >= max_week_tweets-5].groupby(['normal_name','mla_party','tweet_type']).status_id.count()\
    .reset_index().rename(index=str, columns={'status_id':'n_tweets'})
#(now filter to top 10/15 in function below)
top_tweeters['tooltip_text'] = top_tweeters.apply(
    lambda row: f"{row['normal_name']} ({row['mla_party']}): {row['n_tweets']} {'original tweets' if row['tweet_type'] == 'original' else 'retweets'}", axis=1
)

member_retweets = tweets_df[(~tweets_df.is_retweet) & (tweets_df.created_weeksfromJan2020 >= max_week_tweets-5)]\
    .groupby(['normal_name','mla_party'])\
    .agg({'retweet_count': np.mean, 'status_id': len}).reset_index()\
    .query('status_id >= 10')\
    .rename(index=str, columns={'status_id': 'n_original_tweets', 'retweet_count': 'retweets_per_tweet'})\
    .sort_values('retweets_per_tweet', ascending=False)
member_retweets['tooltip_text'] = member_retweets.apply(
    lambda row: f"{row['normal_name']}: {row['retweets_per_tweet']:.1f} retweets per original tweet (from {row['n_original_tweets']} tweets)", axis=1
)

member_tweet_sentiment = tweets_df[(tweets_df.created_weeksfromJan2020 >= max_week_tweets-5) & (tweets_df.sentiment_vader_compound.notnull())]\
    .groupby(['normal_name','mla_party'])\
    .agg({'sentiment_vader_compound': np.mean, 'status_id': len}).reset_index()\
    .query('status_id >= 10').sort_values('sentiment_vader_compound', ascending=False)
member_tweet_sentiment['tooltip_text'] = member_tweet_sentiment.apply(
    lambda row: f"{row['normal_name']}: mean sentiment score = {row['sentiment_vader_compound']:.2f} ({row['status_id']} tweets)", 
    axis=1
)

retweet_rate_last_month = tweets_df[(~tweets_df.is_retweet) & (tweets_df.created_weeksfromJan2020 >= max_week_tweets-5)]\
    .groupby('mla_party')\
    .agg({'retweet_count': np.mean, 'status_id': len}).reset_index()\
    .query('status_id >= 10')\
    .rename(index=str, columns={'status_id': 'n_original_tweets', 'retweet_count': 'retweets_per_tweet'})
retweet_rate_last_month['tooltip_text'] = retweet_rate_last_month.apply(
    lambda row: f"{row['mla_party']}: {row['n_original_tweets']} original tweets with average of {row['retweets_per_tweet']:.1f} retweets per tweet",
    axis=1
)

#Average tweet PCA position
tweets_w_wv_pcas = pd.read_csv(data_dir + 'wv_pca_scored_tweets_apr2019min_to_present.csv',
    dtype={'status_id': object})
#tweet_pca_positions = tweets_df[tweets_df.created_weeksfromJan2020 >= max_week_tweets-5]\
tweet_pca_positions = tweets_df\
    .merge(tweets_w_wv_pcas, on='status_id')\
    .groupby(['normal_name','mla_party'])\
    .agg(
        mean_PC1 = pd.NamedAgg('wv_PC1', np.mean),
        mean_PC2 = pd.NamedAgg('wv_PC2', np.mean),
        num_tweets = pd.NamedAgg('status_id', len)
    ).reset_index()
tweet_pca_positions = tweet_pca_positions[tweet_pca_positions.num_tweets >= 20]
tweet_pca_positions['indiv_page_url'] = ['/individual?mla_name=' + n.replace(' ','+') for n in tweet_pca_positions.normal_name]
tweet_pca_positions['tooltip_text'] = tweet_pca_positions.apply(
    lambda row: f"{row['normal_name']} ({row['num_tweets']} tweets since July 2020)", axis=1
)

#Assembly 
#--------

questions_df = feather.read_dataframe(data_dir + 'niassembly_questions_alltopresent.feather')
questions_df = questions_df.merge(mla_ids[['PersonId','normal_name','PartyName']],
    left_on = 'TablerPersonId', right_on = 'PersonId', how='left')
#Filtering to current session
questions_df = questions_df.query("TabledDate > '2020-02-01'")
#For plot facet labels
questions_df['RequestedAnswerType'] = questions_df.RequestedAnswerType.apply(
    lambda rat: 'Oral' if rat=='oral' else ('Written' if rat=='written' else rat)
)

questioners = questions_df.groupby(['normal_name','PartyName','RequestedAnswerType']).DocumentId.count()\
    .reset_index().rename(index=str, columns={
        'DocumentId': 'Questions asked',
        'RequestedAnswerType': 'Question type'  #for plot titles
    })
#(get top 15 by each question type - now done in function below)
questioners['tooltip_text'] = questioners.apply(
    lambda row: f"{row['normal_name']}: {row['Questions asked']} {row['Question type'].lower()} question{('s' if row['Questions asked'] != 1 else ''):s} asked", axis=1
)

answers_df = feather.read_dataframe(data_dir + 'niassembly_answers_alltopresent.feather')
answers_df = answers_df.merge(mla_ids[['PersonId','normal_name','PartyName']]\
        .rename(index=str, columns={'normal_name': 'Tabler_normal_name', 
            'PersonId': 'TablerPersonId',' PartyName': 'Tabler_party_name'}), 
    on='TablerPersonId', how='inner')
answers_df = answers_df.merge(mla_ids[['PersonId','normal_name','PartyName']]\
        .rename(index=str, columns={'normal_name': 'Minister_normal_name', 
            'PersonId': 'MinisterPersonId', 'PartyName': 'Minister_party_name'}), 
    on='MinisterPersonId', how='left')
answers_df['Days_to_answer'] = (pd.to_datetime(answers_df['AnsweredOnDate']) - pd.to_datetime(answers_df['TabledDate'])).dt.days 
#Filtering to current session
answers_df = answers_df.query("TabledDate > '2020-02-01'")

# minister_answers = answers_df[answers_df.MinisterTitle != 'Assembly Commission']\
#     .groupby(['Minister_normal_name','Minister_party_name']).DocumentId.count().reset_index()\
#     .rename(index=str, columns={'DocumentId':'Questions answered'})
# minister_answers['tooltip_text'] = minister_answers.apply(
#     lambda row: f"{row['Minister_normal_name']}: {row['Questions answered']} answer{('s' if row['Questions answered'] != 1 else ''):s}", axis=1
# )
# minister_answers = minister_answers[minister_answers.Minister_normal_name.isin(mla_minister_roles.keys())]

# minister_time_to_answer = answers_df[answers_df.MinisterTitle != 'Assembly Commission']\
#     .groupby(['Minister_normal_name','Minister_party_name']).Days_to_answer.median().reset_index()\
#     .rename(index=str, columns={'Days_to_answer':'Median days to answer'})
minister_time_to_answer = answers_df[answers_df.MinisterTitle != 'Assembly Commission']\
    .groupby(['Minister_normal_name','Minister_party_name'])\
    .agg(
        median_days_to_answer = pd.NamedAgg('Days_to_answer', np.median),
        num_questions_answered = pd.NamedAgg('Days_to_answer', len)
        ).reset_index()\
    .rename(index=str, columns={
        'median_days_to_answer': 'Median days to answer',  
        'num_questions_answered': 'Questions answered'    #for plot axis title
    })
minister_time_to_answer['tooltip_text'] = minister_time_to_answer.apply(
    lambda row: f"{row['Minister_normal_name']}: median {row['Median days to answer']:g} day{('s' if row['Median days to answer'] != 1 else ''):s}", axis=1
)
minister_time_to_answer = minister_time_to_answer[minister_time_to_answer.Minister_normal_name.isin(mla_minister_roles.keys())]

votes_df = feather.read_dataframe(data_dir + 'division_votes.feather')
vote_results_df = feather.read_dataframe(data_dir + 'division_vote_results.feather')
vote_results_df = vote_results_df.merge(mla_ids[['PersonId','PartyName']], 
    on='PersonId', how='left')
vote_results_df = vote_results_df[vote_results_df.PartyName.notnull()]  #drop a few with missing member and party names
vote_results_df['PartyName'] = vote_results_df.PartyName.apply(lambda p: party_names_translation[p])
votes_df = votes_df.merge(vote_results_df, on='EventId', how='inner')
votes_df = votes_df.merge(mla_ids[['PersonId','normal_name']], on='PersonId', how='inner')
votes_df['DivisionDate'] = pd.to_datetime(votes_df['DivisionDate'], utc=True)
votes_df = votes_df.sort_values('DivisionDate')
#now simplify to print nicer
votes_df['DivisionDate'] = votes_df['DivisionDate'].dt.date
#To pass all votes list, create a column with motion title and url 
#  joined by | so that I can split on this inside the datatable
votes_df['motion_plus_url'] = votes_df.apply(
    lambda row: f"{row['Title']}|http://aims.niassembly.gov.uk/plenary/details.aspx?&ses=0&doc={row['DocumentID']}&pn=0&sid=vd", axis=1)

#Votes PCA
votes_df['vote_num'] = votes_df.Vote.apply(lambda v: {'NO':-1, 'AYE':1, 'ABSTAINED':0}[v]) 
votes_pca_df = votes_df[['normal_name','EventId','vote_num']]\
    .pivot(index='normal_name',columns='EventId',values='vote_num').fillna(0) 
tmp = votes_df.normal_name.value_counts()
those_in_70pc_votes = tmp[tmp >= votes_df.EventId.nunique()*0.70].index
votes_pca_df = votes_pca_df[votes_pca_df.index.isin(those_in_70pc_votes)]

my_pca = PCA(n_components=2, whiten=True)  #doesn't change results but axis units closer to 1
my_pca.fit(votes_pca_df)
#my_pca.explained_variance_ratio_
mlas_2d_rep = pd.DataFrame({'x': [el[0] for el in my_pca.transform(votes_pca_df)],
                            'y': [el[1] for el in my_pca.transform(votes_pca_df)],
                            'normal_name': votes_pca_df.index,
                            'indiv_page_url': ['/individual?mla_name=' + n.replace(' ','+') for n in votes_pca_df.index],
                            'party': [mla_ids.loc[mla_ids.normal_name == n].PartyName.iloc[0] for n in votes_pca_df.index]})

#Votes party unity 
votes_party_unity = votes_df.groupby(['PartyName','EventId'])\
    .agg({'Vote': lambda v: len(np.unique(v)),'normal_name': len}).reset_index()\
    .query('normal_name >= 5')\
    .groupby('PartyName').agg({'Vote': lambda v: 100*np.mean(v==1), 'EventId': len}).reset_index()
votes_party_unity.columns = ['PartyName', 'Percent voting as one', 'n_votes']
votes_party_unity['tooltip_text'] = votes_party_unity.apply(
    lambda row: f"{row['PartyName']} voted as one in {row['Percent voting as one']:.0f}% of {row['n_votes']} votes",
    axis=1
)

#Vote commentary
v_comms = []
for v_id in votes_df.EventId.unique():
    tmp = votes_df.loc[votes_df.EventId==v_id]
    
    vote_date = tmp.DivisionDate.iloc[0]
    vote_subject = tmp.motion_plus_url.iloc[0]
    vote_result = 'PASS' if tmp.Outcome.iloc[0] in ['The Amendment Was Therefore Agreed',
        'The Motion Was Carried','The Motion Was Carried By Cross Community Consent'] else 'FAIL'
    vote_tabler_group = tmp.tabler_personIDs.iloc[0].split(';')
    vote_tabler_group = [mla_ids.loc[mla_ids.PersonId==x,'PartyGroup'].iloc[0] for x in vote_tabler_group]
    vote_tabler_group = vote_tabler_group[0] if len(np.unique(vote_tabler_group))==1 else 'Mixed'

    alli_vote_count = tmp[tmp.PartyName=='Alliance'].Vote.value_counts()
    green_vote_count = tmp[tmp.PartyName=='Green'].Vote.value_counts()
    uni_vote_count = tmp[tmp.Designation=='Unionist'].Vote.value_counts()
    if uni_vote_count.max()/uni_vote_count.sum() >= 0.95:
        u_b_v = uni_vote_count.sort_values().index[-1]
    else:
        u_b_v = 'split'
    nat_vote_count = tmp[tmp.Designation=='Nationalist'].Vote.value_counts()
    if nat_vote_count.max()/nat_vote_count.sum() >= 0.95:
        n_b_v = nat_vote_count.sort_values().index[-1]
    else:
        n_b_v = 'split'
    if (alli_vote_count.max() / alli_vote_count.sum() >= 0.9):
        alli_vote = alli_vote_count.sort_values().index[-1]
    else:
        alli_vote = 'split'
    if (green_vote_count.max() / green_vote_count.sum() >= 0.9):
        green_vote = green_vote_count.sort_values().index[-1]
    else:
        green_vote = 'split'
    #
    dupsf_vote_count = tmp[tmp.PartyName.isin(['DUP','Sinn Fein'])].Vote.value_counts()
    if dupsf_vote_count.max()/dupsf_vote_count.sum() >= 0.95:
        pass
        #print('DUP+SF bloc vote', dupsf_vote_count.index[dupsf_vote_count.argmax()])
    v_comms.append((vote_date, vote_subject, vote_tabler_group, vote_result, u_b_v, n_b_v, alli_vote, green_vote))
v_comms = pd.DataFrame(v_comms, columns=['date','vote_subject','vote_tabler_group','vote_result','uni_bloc_vote','nat_bloc_vote', 'alli_vote','green_vote'])
v_comms['uni_nat_split'] = (v_comms.uni_bloc_vote!='split') & \
    (v_comms.nat_bloc_vote!='split') & (v_comms.nat_bloc_vote!=v_comms.uni_bloc_vote)
v_comms['uni_nat_split'] = v_comms.uni_nat_split.apply(lambda b: 'Yes' if b else 'No')

#TODO we should count when uni_nat_split=True, which side alli, green go with
v_comms = v_comms.sort_values('date', ascending=False)

#Contributions - plenary sessions
plenary_contribs_df = feather.read_dataframe(data_dir + 'plenary_hansard_contribs_201920sessions_topresent.feather')
plenary_contribs_df = plenary_contribs_df[plenary_contribs_df.speaker.isin(mla_ids.normal_name)]

#TODO filter to this session
print('TODO check what sessions are in plenary contribs, topics and emotion')

scored_plenary_contribs_df = pd.read_csv(data_dir + 'lda_scored_plenary_contribs.csv')
plenary_contribs_topic_counts =  scored_plenary_contribs_df.groupby('topic_name').count().reset_index()\
    [['topic_name','session_id']].rename(index=str, columns={'session_id': 'n_contribs'})
#Load model to get the topic top words
with open(data_dir + 'contribs_lda_model.pkl','rb') as f:
    lda_stuff = pickle.load(f)
lda_top5s = [(el[0], ', '.join([f"'{t[0]}'" for t in el[1]])) for el in lda_stuff['topic_model'].show_topics(num_topics=lda_stuff['topic_model'].num_topics, num_words=5, formatted=False)] 
lda_top5s = [(lda_stuff['topic_name_dict'][el[0]], el[1]) for el in lda_top5s]
plenary_contribs_topic_counts = plenary_contribs_topic_counts.merge(
    pd.DataFrame(lda_top5s, columns=['topic_name','top5_words'])
)
plenary_contribs_topic_counts['tooltip_text'] = plenary_contribs_topic_counts.apply(
    lambda row: f"{row['topic_name']}: strongest words are {row['top5_words']}", axis=1
)
#use the colours we get from 'tableau20' but make repeatable and for reuse on indiv page
plenary_contribs_colour_dict = {
    'finance': '#88d27a',
    'justice & legislation': '#e45756',
    'infrastructure & investment': '#83bcb6',
    'government business': '#b79a20',
    'health & social care': '#f2cf5b',
    'education': '#54a24b',
    'politics/parties': '#ff9d98',
    'public sector & unions': '#79706e',
    'brexit/trade': '#f58518',
    'economy': '#ffbf79',
    'agriculture, prisons, industry': '#4c78a8',
    'belfast city': '#9ecae9',
    'housing': '#439894'
}

#plenary contrib emotions
emotions_df = feather.read_dataframe(data_dir + 'plenary_hansard_contribs_emotions_averaged_201920sessions_topresent.feather')
emotions_df = emotions_df.merge(mla_ids[['normal_name','PartyName']], 
    left_on='speaker', right_on='normal_name', how='inner')
emotions_party_agg = emotions_df.groupby(['PartyName','emotion_type']).apply(
    lambda x_grouped: np.average(x_grouped['ave_emotion'], weights=x_grouped['word_count']))\
    .reset_index()\
    .rename(index=str, columns={0:'ave_emotion'})


diary_df = pd.read_csv(data_dir + 'diary_future_events.psv', sep='|')
#Exclude events that have now happened (will run filter again in assembly.html function)
#diary_df = diary_df[diary_df['EventDate'] >= datetime.date.today().strftime('%Y-%m-%d')]
diary_df = diary_df[diary_df['EndTime'] >= datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')]
#Use StartTime to get the date to avoid problems with midnight +/-1h BST
diary_df['EventPrettyDate'] = pd.to_datetime(diary_df['StartTime'], utc=True).dt.strftime('%A, %-d %B')
diary_df['EventName'] = diary_df.apply(
    lambda row: row['OrganisationName']+' Meeting' if row['EventType']=='Committee Meeting' else row['EventType'], 
    axis=1
)
diary_colour_dict = defaultdict(lambda: 'Black')
diary_colour_dict.update(
    {'Committee for Agriculture, Environment and Rural Affairs Meeting': 'SeaGreen',
    'Committee for Communities Meeting': 'DeepSkyBlue',
    'Committee for Education Meeting': 'Salmon',
    'Committee for Finance Meeting': 'Purple',
    'Committee for Health Meeting': 'MediumVioletRed',
    'Committee for Infrastructure Meeting': 'Olive',
    'Committee for Justice Meeting': 'MidnightBlue',
    'Committee for the Economy Meeting': 'DarkSlateGrey',
    'Business Committee Meeting':  'Brown',
    'Sitting of the Assembly': 'Black'}
)
diary_df['EventHTMLColour'] = diary_df.EventName.apply(lambda e: diary_colour_dict[e])

#News 
#----

news_df = pd.concat([
    feather.read_dataframe(data_dir + 'newsriver_articles_maytojuly2020.feather'),
    feather.read_dataframe(data_dir + 'newsriver_articles_ongoing2020.feather')
])
news_df['discoverDate'] = pd.to_datetime(news_df['discoverDate'])
news_df['discoverDate_week'] = news_df.discoverDate.dt.week
news_df = news_df.merge(mla_ids[['normal_name','PartyName','PartyGroup']],
    how = 'inner', on = 'normal_name')
#drop the first and last weeks which could be partial
#news_df = news_df[(news_df.discoverDate_week > news_df.discoverDate_week.min()) &
#    (news_df.discoverDate_week < news_df.discoverDate_week.max())]
news_df = news_df.sort_values('discoverDate', ascending=False)
news_df['date_pretty'] = pd.to_datetime(news_df.discoverDate).dt.strftime('%Y-%m-%d')
news_df['title_plus_url'] = news_df.apply(lambda row: f"{row['title']}|{row['url']}", axis=1)

news_sources = news_df[['url','source','PartyGroup']].drop_duplicates()\
    .groupby(['source','PartyGroup']).url.count().reset_index()\
    .rename(index=str, columns={'url':'News articles'})\
    .sort_values('News articles', ascending=False)
#(now filtering to top 10/15 below in function)
news_sources['tooltip_text'] = news_sources.apply(
    lambda row: f"{row['source']}: {row['News articles']} article{'s' if row['News articles'] != 1 else ''} about {row['PartyGroup'].lower()}s", 
    axis=1
)
#Now trim long names for the axis labels
news_sources['source'] = news_sources.source.apply(lambda n: n if len(n) <= 17 else n[:17]+'...')

#dedup articles by party before calculating averages by party - doesn't make a big difference
news_sentiment_by_party_week = news_df[['discoverDate_week','url','PartyName','sr_sentiment_score']].drop_duplicates()\
    .groupby(['discoverDate_week','PartyName'])\
    .agg({'url': len, 'sr_sentiment_score': np.nanmean}).reset_index()
#news_sentiment_by_party_week = news_sentiment_by_party_week.sort_values(['PartyName','discoverDate_week'], ignore_index=True)
#news_sentiment_by_party_week = news_sentiment_by_party_week[news_sentiment_by_party_week.sr_sentiment_score.notnull()]
#news_sentiment_by_party_week = news_sentiment_by_party_week[news_sentiment_by_party_week.url >= 3]  #OK to keep in now because using smoothing
news_sentiment_by_party_week = news_sentiment_by_party_week[news_sentiment_by_party_week.PartyName.isin(
    ['DUP','Alliance','Sinn Fein','UUP','SDLP'])]
#TODO change this part and do box plot instead
#news_sentiment_by_party_week = news_sentiment_by_party_week.join(
#    news_sentiment_by_party_week.groupby('PartyName', sort=False).sr_sentiment_score\
#        .rolling(7, min_periods=1, center=True).mean().reset_index(0),  #the 0 is vital here
#    rsuffix='_smooth')
#fill in missing weeks before averaging - works for volume only
uniques = [news_sentiment_by_party_week[c].unique().tolist() for c in ['discoverDate_week','PartyName']] 
news_sentiment_by_party_week = pd.DataFrame(product(*uniques), columns=['discoverDate_week','PartyName'])\
    .merge(news_sentiment_by_party_week, on=['discoverDate_week','PartyName'], how='left')
#keep missing sentiment weeks as NA but can fill volumes as zero
news_sentiment_by_party_week['url'] = news_sentiment_by_party_week['url'].fillna(0)

news_sentiment_by_party_week = news_sentiment_by_party_week.join(
    news_sentiment_by_party_week.groupby('PartyName', sort=False).url\
        .rolling(7, min_periods=1, center=True).mean().reset_index(0),  #the 0 is vital here
    rsuffix='_smooth')

news_sentiment_by_party_week['tooltip_text'] = news_sentiment_by_party_week.apply(
    lambda row: f"{row['PartyName']}: {row['url']:g} articles in week (avg = {row['url_smooth']:.1f})", axis=1
)
#drop first and last weeks here instead, so that table still shows the most recent articles
news_sentiment_by_party_week = news_sentiment_by_party_week[(news_sentiment_by_party_week.discoverDate_week > news_sentiment_by_party_week.discoverDate_week.min()) &
    (news_sentiment_by_party_week.discoverDate_week < news_sentiment_by_party_week.discoverDate_week.max())]

#Polls 
#-----

elections_df = pd.merge(pd.read_csv(data_dir + 'election_details.csv'),
    pd.read_csv(data_dir + 'election_results.csv'),
    on = 'election_id', how = 'inner'
)
elections_df['date'] = pd.to_datetime(elections_df.date)
elections_df = elections_df.sort_values(['date','party'], ascending=[False,True])
elections_df['date_year'] = elections_df.date.dt.strftime('%Y')
elections_df = elections_df[elections_df.date > pd.to_datetime('2015-01-01')]
elections_df['tooltip_text'] = elections_df.apply(
    lambda row: f"{row['party']}: {row['pct']:g}% (election; {row['date_year']} {row['election_type']})", axis=1
)

polls_df = pd.merge(pd.read_csv(data_dir + 'poll_details.csv'),
    pd.read_csv(data_dir + 'poll_results.csv'),
    on = 'poll_id', how = 'inner'
)
polls_df = polls_df[polls_df.party != 'Other']  #no point including Other
polls_df['date'] = pd.to_datetime(polls_df.date)
polls_df = polls_df.sort_values(['date','party'], ascending=[False,True])
polls_df['date_pretty'] = polls_df.date.dt.strftime('%Y-%m-%d')
polls_df = polls_df[polls_df.date > pd.to_datetime('2015-01-01')]
polls_df['tooltip_text'] = polls_df.apply(
    lambda row: f"{row['party']}: {row['pct']:g}% (poll; {row['organisation']}, n={row['sample_size']:.0f})", axis=1
)
#Calculate poll averages - now uses both polls and elections
def get_current_avg_poll_pct(polls_df, elections_df, party, current_dt, 
    time_power = 4, 
    assembly_equiv_sample_size = 50000, 
    general_equiv_sample_size = 500):
    local_equiv_sample_size = np.exp((np.log(assembly_equiv_sample_size)+np.log(general_equiv_sample_size))/2)
    
    past_party_elections = elections_df[(elections_df.pct.notnull()) & 
        (elections_df.date < current_dt) & (elections_df.party == party)].copy()
    if past_party_elections.shape[0] > 0:
        past_party_elections['equiv_sample_size'] = past_party_elections.apply(
            lambda row: assembly_equiv_sample_size if row['election_type']=='Assembly' else \
                (general_equiv_sample_size if row['election_type']=='General' else local_equiv_sample_size),
            axis=1
        )
    else:
        past_party_elections['equiv_sample_size'] = []
    
    past_party_polls = polls_df[(polls_df.pct.notnull()) & 
        (polls_df.date < current_dt) & (polls_df.party == party)].copy()
    past_party_polls['equiv_sample_size'] = past_party_polls['sample_size']

    past_party_points = pd.concat([
        past_party_polls[['date','party','pct','equiv_sample_size']],
        past_party_elections[['date','party','pct','equiv_sample_size']]
    ])

    if past_party_points.shape[0] == 0:
        return np.nan
    else:
        past_party_points['poll_age_days'] = (current_dt - past_party_points.date).apply(lambda dt: dt.days)
        past_party_points['wt_factor'] = past_party_points.apply(
            lambda row: 0 if row['poll_age_days'] > 1000 else (row['equiv_sample_size']**0.5) * ((1000-row['poll_age_days'])/1000)**time_power,
            axis=1
        )
        if all(past_party_points['wt_factor'] == 0):
            return np.nan
        else:
            return np.average(past_party_points.pct, weights=past_party_points.wt_factor)

poll_track_timestep = 100 if test_mode else 10
poll_avgs_track = []
earliest_poll_or_election_date = min(polls_df.date.min(), elections_df.date.min())
poll_track_date_range = [datetime.datetime.today() - pd.to_timedelta(i, unit='day') \
    for i in range(0, (datetime.datetime.today() - earliest_poll_or_election_date).days+50, poll_track_timestep)]
for party in polls_df[polls_df.party != 'Other'].party.unique():
    poll_avgs_track.append(pd.DataFrame({'party': party,
        'date': poll_track_date_range,
        'pred_pct': [get_current_avg_poll_pct(polls_df, elections_df, party, d) for d in poll_track_date_range]}))
poll_avgs_track = pd.concat(poll_avgs_track)

#Totals
#------

n_politicians = mla_ids.shape[0]
rank_split_points = [10, n_politicians*0.3, n_politicians*0.7]

n_active_mlas = (mla_ids.role=='MLA').sum()
n_active_mlas_excl_ministers = n_active_mlas - len(mla_minister_roles.keys())

totals_dict = {
    'n_politicians': f"{n_politicians:,}",
    'n_questions': f"{questions_df.DocumentId.nunique():,}",
    'n_answers': f"{answers_df.DocumentId.nunique():,}",
    'n_votes': f"{votes_df.EventId.nunique():,}",
    'n_contributions': f"{plenary_contribs_df.shape[0]:,}",
    'n_tweets': f"{tweets_df.status_id.nunique():,}",
    'n_news': f"{news_df.url.nunique():,}",
    'n_polls': f"{polls_df.poll_id.nunique():,}",
    'last_updated_date': tweets_df.created_at.max().strftime('%A, %-d %B')
}

#Some plot settings 
hover_exclude_opacity_value = 0.4  #when hovered case goes to 1.0, what do rest go to
larger_tick_label_size = 14  #for web mode, on some/all plots

#----

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html',
        totals_dict = totals_dict,
        full_mla_list = sorted(mla_ids.normal_name.tolist()))

@app.route('/what-they-say', methods=['GET'])
def twitter():
    #with open('./static/test_network.json', 'r') as f:
    #    tmp = json.load(f)
    return render_template('twitter.html',
        full_mla_list = sorted(mla_ids.normal_name.tolist()))

@app.route('/what-they-do', methods=['GET'])
def assembly():
    #Exclude events that have now happened
    #diary_df_filtered = diary_df[diary_df['EventDate'] >= datetime.date.today().strftime('%Y-%m-%d')]
    diary_df_filtered = diary_df[diary_df['EndTime'] >= datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')]
    #But not more than 6 items, so as not to clutter page
    diary_df_filtered = diary_df_filtered.head(6)

    votes_list = [e[1].values.tolist() for e in v_comms[['date','vote_subject','vote_tabler_group','vote_result',
        'uni_bloc_vote','nat_bloc_vote','alli_vote','green_vote','uni_nat_split']].iterrows()]
    chances_to_take_a_side = v_comms[(v_comms.uni_nat_split=='Yes')].shape[0]
    alli_num_votes_with_uni = v_comms[(v_comms.uni_nat_split=='Yes') &
        (v_comms.alli_vote==v_comms.uni_bloc_vote)].shape[0]
    alli_num_votes_with_nat = v_comms[(v_comms.uni_nat_split=='Yes') & 
        (v_comms.alli_vote==v_comms.nat_bloc_vote)].shape[0]
    green_num_votes_with_uni = v_comms[(v_comms.uni_nat_split=='Yes') &
        (v_comms.green_vote==v_comms.uni_bloc_vote)].shape[0]
    green_num_votes_with_nat = v_comms[(v_comms.uni_nat_split=='Yes') & 
        (v_comms.green_vote==v_comms.nat_bloc_vote)].shape[0]

    return render_template('assembly.html',
        diary = diary_df_filtered, 
        n_mlas = mlas_2d_rep.shape[0], 
        n_votes = votes_df.EventId.nunique(),
        full_mla_list = sorted(mla_ids.normal_name.tolist()),
        votes_list = votes_list,
        votes_passed_string = f"{(v_comms.vote_result=='PASS').sum()} / {v_comms.shape[0]}",
        uni_nat_split_string = f"{(v_comms.uni_nat_split=='Yes').sum()} / {v_comms.shape[0]}",
        num_uni_nat_split_passes = ((v_comms.uni_nat_split=='Yes') & (v_comms.vote_result=='PASS')).sum(),
        uni_tabled_passed_string = f"{((v_comms.vote_tabler_group=='Unionist') & (v_comms.vote_result=='PASS')).sum()} / {(v_comms.vote_tabler_group=='Unionist').sum()}",
        nat_tabled_passed_string = f"{((v_comms.vote_tabler_group=='Nationalist') & (v_comms.vote_result=='PASS')).sum()} / {(v_comms.vote_tabler_group=='Nationalist').sum()}",
        mix_tabled_passed_string = f"{((v_comms.vote_tabler_group=='Mixed') & (v_comms.vote_result=='PASS')).sum()} / {(v_comms.vote_tabler_group=='Mixed').sum()}",
        alli_like_uni_string = f"{alli_num_votes_with_uni} / {chances_to_take_a_side}",
        alli_like_nat_string = f"{alli_num_votes_with_nat} / {chances_to_take_a_side}",
        green_like_uni_string = f"{green_num_votes_with_uni} / {chances_to_take_a_side}",
        green_like_nat_string = f"{green_num_votes_with_nat} / {chances_to_take_a_side}")

@app.route('/what-we-report', methods=['GET'])
def news():
    if test_mode:
        articles_list = [e[1].values.tolist() for e in news_df.head(50)[['date_pretty','title_plus_url','source','normal_name']].iterrows()]
    else:
        articles_list = [e[1].values.tolist() for e in news_df.head(300)[['date_pretty','title_plus_url','source','normal_name']].iterrows()]

    return render_template('news.html',         
        articles_list = articles_list,
        full_mla_list = sorted(mla_ids.normal_name.tolist()))

@app.route('/how-we-vote', methods=['GET'])
def polls():
    tmp = polls_df.loc[polls_df.pct.notnull()].copy()
    tmp['date_plus_url'] = tmp.apply(
        lambda row: f"{row['date_pretty']}|{row['link']}", axis=1
    )
    tmp = tmp[['date_plus_url','organisation','sample_size','party','pct']]
    return render_template('polls.html',
        poll_results_list = [e[1].values.tolist() for e in tmp.iterrows()],
        full_mla_list = sorted(mla_ids.normal_name.tolist()))

@app.route('/about', methods=['GET'])
def about():
    return render_template('about.html',
        full_mla_list = sorted(mla_ids.normal_name.tolist()))

@app.route('/individual', methods=['GET'])
def indiv():

    args = request.args
    if 'mla_name' in args and args.get('mla_name') != 'Choose MLA...':
        person_selected = True
        person_choice = args.get('mla_name')                
        person_choice_party = mla_ids[mla_ids.normal_name==person_choice].PartyName_long.iloc[0]
        person_name_string = f"{person_choice}"

        #row = mla_ids[mla_ids.normal_name == 'Órlaithí Flynn'].iloc[0]
        row = mla_ids[mla_ids.normal_name == person_choice].iloc[0]
        
        if row['role'] in ['MLA','MP']:
            person_name_string = person_name_string + f" {row['role']}"

        person_is_mla = row['role'] == 'MLA'
        mla_personid = None
        email_address = None
        if person_is_mla:
            image_url = f"http://aims.niassembly.gov.uk/images/mla/{row['PersonId']}_s.jpg"
            mla_personid = row['PersonId']
            email_address = mla_ids[mla_ids.PersonId==mla_personid].AssemblyEmail.iloc[0]
        elif person_choice in mp_api_numbers.keys() and person_choice_party != "Sinn Féin":
            image_url = f"https://members-api.parliament.uk/api/Members/{mp_api_numbers[person_choice]:s}/Portrait?cropType=ThreeFour"
        elif person_choice in member_other_photo_links.keys():
            image_url = member_other_photo_links[person_choice]
        else:
            image_url = '#'

        if person_choice in mla_minister_roles.keys():
            person_name_string += f" ({mla_minister_roles[person_choice]})"

        if person_is_mla:
            person_committee_roles = committee_roles[committee_roles.normal_name==row['normal_name']].apply(
                lambda row: f"{row['Organisation']}{ ' ('+row['Role']+')' if 'Chair' in row['Role'] else ''}", axis=1
            )
        else:
            person_committee_roles = []

        #Better to do last month as I will only be updating weekly
        tweets_last_month = tweets_df[(tweets_df.normal_name==row['normal_name']) &
            (tweets_df.created_at.dt.date > datetime.date.today()-datetime.timedelta(days=30))].shape[0]

        tweets_by_week = tweets_df[tweets_df.normal_name==row['normal_name']].created_at.dt.week.value_counts().sort_index().tolist()

        sample_recent_tweets = tweets_df[(tweets_df.normal_name==row['normal_name']) &
            (~tweets_df.is_retweet)].sort_values('created_at', ascending=False).head(15)[['created_at','text','quoted_status_id']]
        if sample_recent_tweets.shape[0] > 0:
            sample_recent_tweets['created_at'] = sample_recent_tweets.created_at.dt.strftime('%Y-%m-%d')
            sample_recent_tweets['involves_quote'] = sample_recent_tweets.quoted_status_id.apply(lambda s: s is not None) 
            sample_recent_tweets['quoted_url'] = sample_recent_tweets.apply(
                lambda row: re.findall('//t.*', row['text'])[0] if row['involves_quote'] else '', axis=1
            )
            sample_recent_tweets['text'] = sample_recent_tweets.text.str.replace('//t.*','')
            sample_recent_tweets = sample_recent_tweets[sample_recent_tweets.text != '']
        if sample_recent_tweets.shape[0] > 5:
            sample_recent_tweets = sample_recent_tweets.sample(5)\
            .sort_values('created_at', ascending=False)

        if sum(tweets_df.normal_name==row['normal_name']) > 0:
            twitter_handle = tweets_df[tweets_df.normal_name==row['normal_name']].screen_name.iloc[0]

            tweet_volume_rank = tweets_df['normal_name'].value_counts().index.get_loc(row['normal_name']) + 1
            #
            if tweet_volume_rank <= rank_split_points[0]:
                tweet_volume_rank_string = f"Tweets <b>very frequently</b>"
            elif tweet_volume_rank <= rank_split_points[1]:
                tweet_volume_rank_string = f"Tweets <b>fairly frequently</b>"
            elif tweet_volume_rank <= rank_split_points[2]:
                tweet_volume_rank_string = f"Tweets at an <b>average rate</b>"
            else:
                tweet_volume_rank_string = f"<b>Doesn't tweet very often</b>"
            tweet_volume_rank_string += f"<br />(<b>#{tweet_volume_rank} / {n_politicians}</b> in total tweets since 1 July 2020)"
        else:
            twitter_handle = None
            tweet_volume_rank_string = "Doesn't tweet at all"
        member_tweet_volumes = tweets_df['normal_name'].value_counts().values.tolist()

        if sum(member_retweets['normal_name'] == row['normal_name']) > 0:
            retweet_rate = member_retweets[member_retweets['normal_name'] == row['normal_name']]['retweets_per_tweet'].iloc[0]
            retweet_rate_rank = (member_retweets['normal_name'] == row['normal_name']).values.argmax()+1
            if retweet_rate_rank <= 10:
                retweet_rate_rank_string = f"<b>High</b> Twitter impact"
            elif retweet_rate_rank <= 0.3*member_retweets.shape[0]:
                retweet_rate_rank_string = f"<b>Fairly high</b> Twitter impact"
            elif retweet_rate_rank <= 0.7*member_retweets.shape[0]:
                retweet_rate_rank_string = f"<b>Average</b> Twitter impact"
            else:
                retweet_rate_rank_string = f"<b>Low</b> Twitter impact"
            retweet_rate_rank_string += f"<br />(<b>#{retweet_rate_rank} / {member_retweets.shape[0]}</b> in retweets per original tweet)"
        else:
            retweet_rate_rank_string = 'n/a'
            retweet_rate = None
        member_retweet_rates = member_retweets['retweets_per_tweet'].tolist()

        if row['normal_name'] in member_tweet_sentiment.normal_name.tolist():
            tweet_positivity = member_tweet_sentiment[member_tweet_sentiment['normal_name'] == row['normal_name']]['sentiment_vader_compound'].iloc[0]
            tweet_positivity_rank = (member_tweet_sentiment['normal_name'] == row['normal_name']).values.argmax()+1
            if tweet_positivity_rank <= 10:
                tweet_positivity_rank_string = f"Tweets <b>very positive</b> messages"
            elif tweet_positivity_rank <= 0.3*member_tweet_sentiment.shape[0]:
                tweet_positivity_rank_string = f"Tweets <b>fairly positive</b> messages"
            elif tweet_positivity_rank <= 0.7*member_tweet_sentiment.shape[0]:
                tweet_positivity_rank_string = f"Tweets messages of <b>average sentiment</b>"
            else:
                tweet_positivity_rank_string = f"Tweets <b>relatively negative</b> messages"
            tweet_positivity_rank_string += f"<br />(<b>#{tweet_positivity_rank} / {member_tweet_sentiment.shape[0]}</b> for tweet positivity)"
        else:
            tweet_positivity_rank_string = 'n/a'
            tweet_positivity = None
        member_tweet_positivities = member_tweet_sentiment['sentiment_vader_compound'].tolist()

        news_articles_last_month = news_df[(news_df.normal_name==row['normal_name']) &
            (news_df.discoverDate.dt.date > datetime.date.today()-datetime.timedelta(days=30))].shape[0]

        news_articles_by_week = news_df[news_df.normal_name==row['normal_name']].discoverDate.dt.week.value_counts().sort_index().tolist()

        #TODO last 5 news articles

        mla_votes_list = []
        tmp = votes_df.loc[votes_df['normal_name'] == row['normal_name'], ['DivisionDate','motion_plus_url','Vote']].sort_values('DivisionDate', ascending=False)
        if tmp.shape[0] > 0:
            mla_votes_list = [e[1].values.tolist() for e in tmp.iterrows()]

        votes_present_numbers = (sum(votes_df['normal_name'] == row['normal_name']), votes_df.EventId.nunique())
        votes_present_string = f"<b>{votes_present_numbers[0]}" + \
            f" / {votes_present_numbers[1]} votes</b> since {votes_df.DivisionDate.min().strftime('%d %B %Y')}"
        

        num_questions = (questions_df['normal_name'] == row['normal_name']).sum()
        member_question_volumes = questions_df['normal_name'].value_counts().values.tolist()
        if num_questions > 0:
            questions_rank = questions_df['normal_name'].value_counts().index.get_loc(row['normal_name']) + 1
            questions_rank_string = f"<b>#{questions_rank} / {n_active_mlas}</b>"
        else:
            questions_rank_string = f"<b>#{questions_df.normal_name.nunique()+1}-{n_active_mlas} / {n_active_mlas}</b>"
        #can't consistently work out the denominator excluding ministers so just use the 90

        #TODO last 5 questions asked

        num_plenary_contribs = (plenary_contribs_df['speaker'] == row['normal_name']).sum()
        if num_plenary_contribs > 0:
            plenary_contribs_rank = plenary_contribs_df['speaker'].value_counts().index.get_loc(row['normal_name']) + 1
            plenary_contribs_rank_string = f"<b>#{plenary_contribs_rank} / {max(n_active_mlas, plenary_contribs_df.speaker.nunique())}</b>"
        else:
            plenary_contribs_rank_string = f"<b>#{plenary_contribs_df.speaker.nunique()+1}-{n_active_mlas} / {n_active_mlas}</b>"
        member_contribs_volumes = plenary_contribs_df['speaker'].value_counts().values.tolist()

        top_contrib_topics = scored_plenary_contribs_df[scored_plenary_contribs_df['speaker'] == row['normal_name']]\
            .topic_name.value_counts(normalize=True, dropna=False)
        top_contrib_topics = top_contrib_topics[top_contrib_topics.index != 'misc./none']
        #send topic|pct for font size|color for font
        if len(top_contrib_topics) >= 3:
            top_contrib_topic_list = [f"{top_contrib_topics.index[0]} ({top_contrib_topics.values[0]*100:.0f}%)|{36*max(min(top_contrib_topics.values[0]/0.4,1), 16/36):.0f}|{plenary_contribs_colour_dict[top_contrib_topics.index[0]]}",
                f"{top_contrib_topics.index[1]} ({top_contrib_topics.values[1]*100:.0f}%)|{36*max(min(top_contrib_topics.values[1]/0.4,1), 16/36):.0f}|{plenary_contribs_colour_dict[top_contrib_topics.index[1]]}",
                f"{top_contrib_topics.index[2]} ({top_contrib_topics.values[2]*100:.0f}%)|{36*max(min(top_contrib_topics.values[2]/0.4,1), 16/36):.0f}|{plenary_contribs_colour_dict[top_contrib_topics.index[2]]}"
            ]
            print(top_contrib_topic_list)
        else:
            top_contrib_topic_list = []

    else:
        person_selected = False
        person_is_mla = False
        mla_personid = None
        person_name_string = None
        person_choice_party = None
        person_committee_roles = []
        image_url = None
        twitter_handle = None
        email_address = None
        tweets_last_month = None
        tweets_by_week = []
        tweet_volume_rank_string = None
        member_tweet_volumes = []
        retweet_rate_rank_string = None
        retweet_rate = None
        member_retweet_rates = []
        tweet_positivity_rank_string = None
        tweet_positivity = None
        member_tweet_positivities = []
        sample_recent_tweets = None
        news_articles_last_month = None
        news_articles_by_week = []
        mla_votes_list = None
        votes_present_string = None
        votes_present_numbers = []
        num_questions = None
        questions_rank_string = None
        member_question_volumes = []
        num_plenary_contribs = None
        plenary_contribs_rank_string = None
        member_contribs_volumes = []
        top_contrib_topic_list = []


    return render_template('indiv.html', 
        person_selected = person_selected,
        person_is_mla = person_is_mla,
        mla_personid = mla_personid,
        full_mla_list = sorted(mla_ids.normal_name.tolist()),
        person_name_string = person_name_string,
        person_party = person_choice_party,
        person_committee_roles = person_committee_roles,
        image_url = image_url,
        twitter_handle = twitter_handle,
        email_address = email_address,
        tweets_last_month = tweets_last_month, 
        tweets_by_week = tweets_by_week,
        tweet_volume_rank_string = tweet_volume_rank_string,
        member_tweet_volumes = member_tweet_volumes,
        retweet_rate_rank_string = retweet_rate_rank_string,
        retweet_rate = retweet_rate,
        member_retweet_rates = member_retweet_rates,
        tweet_positivity_rank_string = tweet_positivity_rank_string,
        tweet_positivity = tweet_positivity,
        member_tweet_positivities = member_tweet_positivities,
        sample_recent_tweets = sample_recent_tweets,
        news_articles_last_month = news_articles_last_month,
        news_articles_by_week = news_articles_by_week,
        mla_votes_list = mla_votes_list,
        votes_present_string = votes_present_string,
        votes_present_numbers = votes_present_numbers,
        num_questions = num_questions,
        questions_rank_string = questions_rank_string,
        member_question_volumes = member_question_volumes,
        num_plenary_contribs = num_plenary_contribs,
        plenary_contribs_rank_string = plenary_contribs_rank_string,
        member_contribs_volumes = member_contribs_volumes,
        top_contrib_topic_list = top_contrib_topic_list)


#Plots are not pages themselves
def add_grey_legend(plot, orient='top-right', columns=1, mobile_mode=False):
    return plot.configure_legend(
        strokeColor='gray',
        fillColor='#EEEEEE',
        padding = 7 if mobile_mode else 10,
        cornerRadius=10,
        orient = orient,
        columns = columns,
        labelFontSize = 8 if mobile_mode else 11,
        symbolSize = 40 if mobile_mode else 70,
        titleFontSize = 9 if mobile_mode else 12
    )

#Most minister answers bars
# @app.route('/data/plot_minister_answers_bars')
# def plot_minister_answers_bars_fn():
#     selection = altair.selection_single(on='mouseover', empty='all')
#     plot = altair.Chart(minister_answers).mark_bar()\
#         .add_selection(selection)\
#         .encode(#x='Minister_normal_name', 
#             y='Questions answered',
#             x=altair.Y('Minister_normal_name', sort='-y', axis = altair.Axis(title=None)),
#             color = altair.Color('Minister_party_name', 
#                 scale=altair.Scale(
#                     domain=party_colours[party_colours.party_name.isin(minister_answers.Minister_party_name)]['party_name'].tolist(), 
#                     range=party_colours[party_colours.party_name.isin(minister_answers.Minister_party_name)]['colour'].tolist()
#                     )),
#             #opacity = altair.condition(selection, altair.value(1), altair.value(0.3)),
#             tooltip = 'tooltip_text')\
#         .properties(title = ' ', width='container', height=250)
#     #plot1 = plot1.configure_view(discreteWidth=800, continuousHeight=500)
#     #plot = plot.configure_title(fontSize=16, font='Courier')
#     plot = plot.configure_legend(disable=True)
 
#     return plot.to_json()

#
@app.route('/data/plot_minister_answer_times')
def plot_minister_answer_times_fn():
    #Problem with sorting two layer chart in Vega
    #Fixed in Altair 3 according to here https://github.com/altair-viz/altair/issues/820
    #  but seems not to be in my case
    #Sorting values first also seems not to work
    #Another workaround is to do second layer with independent axis, and hide labels and ticks,
    #  and this does work (https://github.com/altair-viz/altair/issues/820#issuecomment-386856394)

    plot = altair.Chart(minister_time_to_answer).mark_bar(size=3)\
        .encode(x=altair.X('Questions answered', axis=altair.Axis(grid=True)),
            y=altair.Y('Minister_normal_name', sort=altair.EncodingSortField(order='ascending', field='Questions answered'),
                axis = altair.Axis(title=None)),
            color = altair.Color('Minister_party_name', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(minister_time_to_answer.Minister_party_name)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(minister_time_to_answer.Minister_party_name)]['colour'].tolist()
                    )))#\
        #.properties(title = ' ', width='container', height=250)
    
    # #Lose the axis ordering if add this on top
    #plot_b = plot.mark_circle(size=80)#\
        #.encode(x='Median days to answer',
        #    y=altair.Y('Minister_normal_name', sort=altair.EncodingSortField(order='ascending', field='tmp_sort_field')))
            #y=altair.Y('Minister_normal_name', sort='x'),
    #plot = plot + plot_b

    #default opacity is < 1 for circles so have to set to 1 to match bars
    plot_b = altair.Chart(minister_time_to_answer).mark_circle(size=200, opacity=1)\
        .encode(x='Questions answered',
            y=altair.Y('Minister_normal_name', sort=altair.SortField(order='ascending', field='Questions answered'),
                axis = altair.Axis(labels=False, ticks=False, title=None)),
            color = altair.Color('Minister_party_name', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(minister_time_to_answer.Minister_party_name)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(minister_time_to_answer.Minister_party_name)]['colour'].tolist()
                    )),
            size = 'Median days to answer',
            tooltip = 'tooltip_text')#\
        #.properties(title = '', width=300, height=250)
    plot = altair.layer(plot, plot_b, data=minister_time_to_answer).resolve_scale(y='independent')
    plot = plot.properties(title = ' ', width='container', height=250)

    plot = plot.configure_title(fontSize=16, font='Courier')
    plot = plot.configure_legend(disable=True)
    # plot = plot.configure_legend(
    #     strokeColor='gray',
    #     fillColor='#EEEEEE',
    #     padding=10,
    #     cornerRadius=10,
    #     orient='top-right'
    # )

    # base = altair.Chart(minister_time_to_answer.sort_values('Median days to answer'))\
    #     .encode(x='Median days to answer',
    #         #y=altair.Y('Minister_normal_name', sort='x'),
    #         y=altair.Y('Minister_normal_name', sort=altair.SortField(order='ascending', field='Median days to answer')),
    #         color = altair.Color('Minister_party_name', 
    #             scale=altair.Scale(
    #                 domain=party_colours[party_colours.party_name.isin(minister_answers.Minister_party_name)]['party_name'].tolist(), 
    #                 range=party_colours[party_colours.party_name.isin(minister_answers.Minister_party_name)]['colour'].tolist()
    #                 )))\
    #     .properties(title = 'Minister average time to answer question', width=500, height=300)
    # plot = base.mark_bar(size=2) + base.mark_circle()

    #plot_l.layer = [plot, plot_b]

    return plot.to_json()
#TODO could turn to scatter of n answered vs time
#  or boxplot/ridgelines

#Most questions asked, split by written/oral
@app.route('/data/plot_questions_asked_web')
def plot_questions_asked_fn_web():
    return plot_questions_asked_fn()

@app.route('/data/plot_questions_asked_mobile')
def plot_questions_asked_fn_mobile():
    return plot_questions_asked_fn(mobile_mode = True)

def plot_questions_asked_fn(mobile_mode = False):
    plot_df = questioners.sort_values('Questions asked', ascending=False)\
        .groupby('Question type').head(8 if mobile_mode else 12)

    #Opacity change on selection doesn't add anything
    #selection = altair.selection_single(on='mouseover', empty='all')
    #.add_selection(selection)\
    #opacity = altair.condition(selection, altair.value(1), altair.value(hover_exclude_opacity_value)),

    plot = altair.Chart(plot_df).mark_bar(opacity=1)\
        .encode(
            y=altair.Y('Questions asked'),
            x=altair.Y('normal_name', sort='-y', axis = altair.Axis(title=None)),
            color = altair.Color('PartyName', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(plot_df.PartyName)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(plot_df.PartyName)]['colour'].tolist()
                    ), legend=None),
            facet = altair.Facet('Question type:N', columns=1),
            tooltip='tooltip_text:N')\
        .resolve_scale(x='independent', y = 'independent')\
        .properties(title = '', width='container', height=250)
    #plot = plot.configure_title(fontSize=20, font='Courier')
    #plot = add_grey_legend(plot)
    # plot = plot.configure_legend(
    #     direction='horizontal', 
    #     orient='top',
    #     strokeColor='gray',
    #     fillColor='#EEEEEE',
    #     padding=10,
    #     cornerRadius=10
    # )
    #plot = plot.configure_legend(disable=True)

    return plot.to_json()

#Tweet volume and retweets scatter of parties
@app.route('/data/plot_party_tweets_scatter')
def plot_party_tweets_scatter_fn():

    plot = altair.Chart(retweet_rate_last_month).mark_circle(size=140, opacity=1)\
        .encode(x=altair.X('n_original_tweets', 
                    axis=altair.Axis(title='Number of original tweets')),
            y=altair.Y('retweets_per_tweet', axis=altair.Axis(title='Retweets per tweet')),
            color = altair.Color('mla_party', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(retweet_rate_last_month.mla_party)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(retweet_rate_last_month.mla_party)]['colour'].tolist()
                    )),
                 #legend=altair.Legend(title="Party")),
            tooltip = 'tooltip_text:N')\
        .properties(title = '', width=500, height=500)        

    #start fresh layer, otherwise fontSize is locked to size of points
    text = altair.Chart(retweet_rate_last_month).mark_text(
        align='left',
        baseline='middle',
        dx=6, dy=-6,
        fontSize=11
    ).encode(
        text='mla_party', x='n_original_tweets', y='retweets_per_tweet'
    )
    plot += text

    plot = plot.configure_legend(disable=True)
    plot = plot.configure_title(fontSize=20, font='Courier')
    #plot = plot.configure_axis(labelFontSize=14)
    #plot = plot.configure_axisX(tickCount = x_ticks)

    return plot.to_json()

#Most tweets by person
@app.route('/data/plot_user_tweetnum_web')
def plot_user_tweetnum_fn_web():
    return plot_user_tweetnum_fn()
@app.route('/data/plot_user_tweetnum_mobile')
def plot_user_tweetnum_fn_mobile():
    return plot_user_tweetnum_fn(mobile_mode = True)

def plot_user_tweetnum_fn(mobile_mode = False):
    top15_tweeters = tweets_df[tweets_df.created_weeksfromJan2020 >= max_week_tweets-5].groupby('normal_name').status_id.count()\
        .reset_index().sort_values('status_id')\
        .tail(10 if mobile_mode else 15)\
        .normal_name.tolist()
    top_tweeters_plot_df = top_tweeters[top_tweeters.normal_name.isin(top15_tweeters)]

    selection = altair.selection_single(on='mouseover', empty='all')
    plot = altair.Chart(top_tweeters_plot_df).mark_bar()\
        .add_selection(selection)\
        .encode(
            y=altair.Y('n_tweets', axis=altair.Axis(title='Number of tweets')),
            x=altair.Y('normal_name', sort='-y', axis = altair.Axis(title=None)),
            color = altair.Color('tweet_type', 
                scale=altair.Scale(
                    domain=['original','retweet'],
                    range=['Peru','SlateGrey']
                ), legend=altair.Legend(title="")),
            opacity = altair.condition(selection, altair.value(1), altair.value(0.3)),
            tooltip='tooltip_text:N')\
        .properties(title = '', 
            width = 'container', 
            height = 200 if mobile_mode else 300)

    plot = add_grey_legend(plot, mobile_mode = mobile_mode)

    plot = plot.to_json()

    return plot

#Highest retweet rates - need to take .head(15) here
@app.route('/data/plot_user_retweet_web')
def plot_user_retweet_fn_web():
    return plot_user_retweet_fn()

@app.route('/data/plot_user_retweet_mobile')
def plot_user_retweet_fn_mobile():
    return plot_user_retweet_fn(mobile_mode = True)

def plot_user_retweet_fn(mobile_mode = False):
    plot_df = member_retweets.head(10 if mobile_mode else 15)
    selection = altair.selection_single(on='mouseover', empty='all')
    plot = altair.Chart(plot_df).mark_bar()\
        .add_selection(selection)\
        .encode(
            y=altair.Y('retweets_per_tweet', axis=altair.Axis(title='Retweets per tweet')),
            x=altair.Y('normal_name', sort='-y', axis = altair.Axis(title=None)),
            color = altair.Color('mla_party', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(plot_df.mla_party)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(plot_df.mla_party)]['colour'].tolist()
                    ), legend=altair.Legend(title='')),
            tooltip='tooltip_text:N')\
        .properties(title = '', 
            width = 'container', 
            height = 200 if mobile_mode else 300)

    plot = add_grey_legend(plot, mobile_mode = mobile_mode)

    return plot.to_json()

#Highest and lowest tweet sentiment scores
@app.route('/data/plot_user_tweet_sentiment_web')
def plot_user_tweet_sentiment_fn_web():
    return plot_user_tweet_sentiment_fn()

@app.route('/data/plot_user_tweet_sentiment_mobile')
def plot_user_tweet_sentiment_fn_mobile():
    return plot_user_tweet_sentiment_fn(mobile_mode = True)

def plot_user_tweet_sentiment_fn(mobile_mode = False):
    n_of_each_to_plot = 6 if mobile_mode else 10
    df_to_plot = member_tweet_sentiment[member_tweet_sentiment.normal_name.isin(
        member_tweet_sentiment.sort_values('sentiment_vader_compound').head(n_of_each_to_plot).normal_name.tolist() +
        member_tweet_sentiment.sort_values('sentiment_vader_compound').tail(n_of_each_to_plot).normal_name.tolist()
    )].sort_values('sentiment_vader_compound')
    #df_to_plot['group'] = ['+ve']*n_of_each_to_plot + ['-ve']*n_of_each_to_plot
    
    most_pos_text_y2 = df_to_plot.sentiment_vader_compound.min()*0.22 #some way below the zero line relative to the lowest point drawn
    most_neg_text_y2 = df_to_plot.sentiment_vader_compound.iloc[int(0.5*n_of_each_to_plot)]+0.04
    df_to_plot['y2'] = [most_neg_text_y2]*n_of_each_to_plot + [most_pos_text_y2]*n_of_each_to_plot
    df_to_plot['text'] = ['']*df_to_plot.shape[0]
    df_to_plot.iloc[int(n_of_each_to_plot/2-1), df_to_plot.columns.get_loc('text')] = 'Most negative'
    df_to_plot.iloc[df_to_plot.shape[0]-int(n_of_each_to_plot/2), df_to_plot.columns.get_loc('text')] = 'Most positive'
    df_to_plot['names_numbered'] = range(df_to_plot.shape[0])

    selection = altair.selection_single(on='mouseover', empty='all')
    #size = 280/n_of_each_to_plot)\
    plot = altair.Chart(df_to_plot).mark_bar()\
        .add_selection(selection)\
        .encode(
            y=altair.Y('sentiment_vader_compound', 
                axis=altair.Axis(title='Mean tweet sentiment score')),
                #scale=altair.Scale(domain=[-1,1])),
            x=altair.X('normal_name', sort=altair.EncodingSortField(field='sentiment_vader_compound', op='max'), axis = altair.Axis(title=None)),
            color = altair.Color('mla_party',
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(df_to_plot.mla_party)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(df_to_plot.mla_party)]['colour'].tolist()
                    ),
                legend = altair.Legend(title = '')),
            tooltip='tooltip_text:N')\
        .properties(title = '', 
            width = 'container', 
            height = 250 if mobile_mode else 400)

    text = altair.Chart(df_to_plot).mark_text(
        align='center',
        baseline='middle',
        dx=0, 
        dy=0,
        fontSize = 13 if mobile_mode else 15
    ).encode(text='text', y='y2',
        x=altair.X('normal_name', sort=altair.EncodingSortField(field='sentiment_vader_compound', op='max')), 
    ).transform_filter(altair.datum.text != '')
    plot += text

    #Can't find a way to have the line offset correctly on all display sizes
    #dividing_line = altair.Chart(df_to_plot).mark_rule(xOffset=-0.6*280/n_of_each_to_plot, strokeDash=[10,3])\
    #    .encode(x=altair.X('normal_name', sort=altair.EncodingSortField(field='sentiment_vader_compound', op='max')))\
    #    .transform_filter(altair.datum.names_numbered == n_of_each_to_plot)
    #dividing_line = altair.Chart(pd.DataFrame({'names_numbered': [9.5]})).mark_rule(xOffset=0, strokeDash=[10,3])\
    #    .encode(x=altair.X('names_numbered', sort=altair.EncodingSortField(field='sentiment_vader_compound', op='max')))
    #plot += dividing_line

    plot = add_grey_legend(plot, orient = 'top-left', mobile_mode = mobile_mode)

    return plot.to_json()

#Tweets PCA scatter of politicians}
@app.route('/data/plot_tweet_pca_all_mlas_web')
def plot_tweet_pca_all_mlas_fn_web():
    return plot_tweet_pca_all_mlas_fn()

@app.route('/data/plot_tweet_pca_all_mlas_mobile')
def plot_tweet_pca_all_mlas_fn_mobile():
    return plot_tweet_pca_all_mlas_fn(mobile_mode = True)

def plot_tweet_pca_all_mlas_fn(mobile_mode = False):

    plot = altair.Chart(tweet_pca_positions).mark_circle(opacity=0.6)\
        .encode(x=altair.X('mean_PC1', 
                    #axis=altair.Axis(title='Principal component 1 (explains 12% variance)', labels=False)),
                    axis=altair.Axis(title=['<---- more asking for good governance','             more historical references or Irish language ---->'], labels=False)),
            y=altair.Y('mean_PC2', #axis=altair.Axis(title='Principal component 2 (explains 2% variance)', labels=False)),
                axis=altair.Axis(title='more praising others <----    ----> more brexit and party politics', labels=False)),
            color = altair.Color('mla_party', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(tweet_pca_positions.mla_party)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(tweet_pca_positions.mla_party)]['colour'].tolist()
                    ),
                legend=altair.Legend(title='')),
            size = altair.Size('num_tweets', scale=altair.Scale(range=[30 if mobile_mode else 60, 250 if mobile_mode else 500]),
                legend=None),
            tooltip = 'tooltip_text:N')\
        .properties(title = '', 
            width = 'container', 
            height = 250 if mobile_mode else 450)  #stretch x-axis because PC1 explains more variance?

    if not mobile_mode:
        plot = plot.encode(href = 'indiv_page_url:N')

    plot = add_grey_legend(plot, orient = 'top', columns = 2 if mobile_mode else 4,
        mobile_mode = mobile_mode)
    # plot = plot.configure_legend(
    #     direction='horizontal', 
    #     orient='top',
    #     strokeColor='gray',
    #     fillColor='#EEEEEE',
    #     padding = 7 if mobile_mode else 10,
    #     cornerRadius=10,
    #     columns = 2 if mobile_mode else 4)
    plot = plot.configure_axis(titleFontSize=10)

    return plot.to_json()

#Top news sources overall
@app.route('/data/plot_news_sources_web')
def plot_news_sources_fn_web():
    return plot_news_sources_fn()

@app.route('/data/plot_news_sources_mobile')
def plot_news_sources_fn_mobile():
    return plot_news_sources_fn(mobile_mode = True)

def plot_news_sources_fn(mobile_mode = False):
    topN_sources = news_sources.groupby('source').agg({'News articles': sum})\
        .reset_index().sort_values('News articles')\
        .tail(10 if mobile_mode else 15)\
        .source.tolist()
    news_sources_plot_df = news_sources[news_sources.source.isin(topN_sources)]

    selection = altair.selection_single(on='mouseover', empty='all')
    plot = altair.Chart(news_sources_plot_df).mark_bar()\
        .add_selection(selection)\
        .encode(#x='Minister_normal_name', 
            y=altair.Y('News articles'),
            x=altair.Y('source', sort='-y', axis = altair.Axis(title=None)),
            color = altair.Color('PartyGroup', 
                scale=altair.Scale(
                    domain=['Unionist','Other','Nationalist'],
                    range=['RoyalBlue','Moccasin','LimeGreen']
                ), legend = altair.Legend(title = 'Mentioning')),
            opacity = altair.condition(selection, altair.value(1), altair.value(hover_exclude_opacity_value)),
            tooltip='tooltip_text:N')\
        .properties(title = '', 
            width = 'container', 
            height = 200 if mobile_mode else 300)
    plot = add_grey_legend(plot, mobile_mode = mobile_mode)

    return plot.to_json() 

#News sentiment by party and week
@app.route('/data/plot_news_volume_web')
def plot_news_volume_fn_web():
    return shared_plot_news_fn(news_sentiment_by_party_week, 'url_smooth', 'Number of mentions', '')

@app.route('/data/plot_news_volume_mobile')
def plot_news_volume_fn_mobile():
    return shared_plot_news_fn(news_sentiment_by_party_week, 'url_smooth', 'Number of mentions', '', mobile_mode=True)

def shared_plot_news_fn(news_sentiment_by_party_week, y_variable, y_title, title, mobile_mode=False):
    x_ticks = news_sentiment_by_party_week.discoverDate_week.nunique()
    x_range = (news_sentiment_by_party_week.discoverDate_week.min(),
        news_sentiment_by_party_week.discoverDate_week.max())

    plot = altair.Chart(news_sentiment_by_party_week).mark_line(size=5)\
        .encode(x=altair.X('discoverDate_week', 
                    scale=altair.Scale(domain=x_range),
                    axis=altair.Axis(title='2020 week number')),
            y=altair.Y(y_variable, axis=altair.Axis(title=y_title, minExtent=10, maxExtent=100)),
            color = altair.Color('PartyName', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(news_sentiment_by_party_week.PartyName)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(news_sentiment_by_party_week.PartyName)]['colour'].tolist()
                    ),
                legend=altair.Legend(title='')),
            tooltip='tooltip_text:N')\
        .properties(title = title,
            width = 'container', 
            height = 200 if mobile_mode else 300)
    #plot = add_grey_legend(plot, orient='top')
    if not mobile_mode:
        plot = plot.configure_axis(labelFontSize = larger_tick_label_size)
        plot = plot.configure_axisX(tickCount = x_ticks)
    #To line up the two plots vertically on page, assuming sentiment one has -0.01 values
    #if y_variable == 'url_smooth':
    #    plot = plot.configure_axisY(titlePadding=20, labelPadding=10)
    plot = plot.configure_legend(
        direction='horizontal', 
        orient='top',
        strokeColor='gray',
        fillColor='#EEEEEE',
        padding = 7 if mobile_mode else 10,
        cornerRadius = 10,
        columns = 2 if mobile_mode else 5
    )

    return plot.to_json()

@app.route('/data/plot_news_sentiment')
def plot_news_sentiment_fn(mobile_mode = False):
    #There seems to be a bug in Vega boxplot continuous title, possibly 
    #  related to it being a layered plot - gives title of 'variable, mytitle, mytitle'
    #Can't avoid printing the variable name!
    news_sentiment_by_party_week['Mean sentiment score'] = news_sentiment_by_party_week.sr_sentiment_score
    #work out order manually - easier than figuring out the altair method
    party_order = news_sentiment_by_party_week.groupby('PartyName').sr_sentiment_score.mean().sort_values(ascending=False).index.tolist()

    plot = altair.Chart(news_sentiment_by_party_week).mark_boxplot()\
        .encode(y = altair.Y('PartyName:N', axis=altair.Axis(title=''), sort=party_order),
            x = altair.X('Mean sentiment score:Q', axis=altair.Axis(title='')),
            color = altair.Color('PartyName', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(news_sentiment_by_party_week.PartyName)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(news_sentiment_by_party_week.PartyName)]['colour'].tolist()
                    ),
                legend=None),
            tooltip=altair.Tooltip(field='Mean sentiment score', type='quantitative', aggregate='mean', format='.3'))\
        .properties(title = '',
            width='container', 
            height = 200 if mobile_mode else 300)
    #use the aggregated tooltip to avoid printing the full summary
    #  with long floats and variable names

    plot = plot.configure_legend(
        direction='horizontal', 
        orient='top',
        strokeColor='gray',
        fillColor='#EEEEEE',
        padding=10,
        cornerRadius=10
    )

    return plot.to_json()


#Polls tracker
@app.route('/data/polls_plot_web')
def polls_plot_fn_web():
    return polls_plot_fn()
@app.route('/data/polls_plot_mobile')
def polls_plot_fn_mobile():
    return polls_plot_fn(mobile_mode = True)

def polls_plot_fn(mobile_mode = False):
    #x_ticks = polls_df.discoverDate_week.nunique()
    #x_range = (polls_df.date.min() - pd.to_timedelta('90 days'),
    #    polls_df.date.max() + pd.to_timedelta('90 days'))
    #selection = altair.selection_single(on='mouseover', empty='all')
    selection2 = altair.selection_single(fields=['pct_type'], bind='legend')

    polls_df['pct_type'] = 'polls'
    elections_df['pct_type'] = 'elections'
    joint_plot_df = pd.concat([
        polls_df[['date','pct','party','tooltip_text','pct_type']],
        elections_df[['date','pct','party','tooltip_text','pct_type']]
    ])
    #Alternative to the scroll option is to only show last 3 years on mobile
    if False and mobile_mode:
        years_to_show_on_mobile = 3
        joint_plot_df = joint_plot_df[joint_plot_df.date >= (datetime.datetime.today() - datetime.timedelta(days=years_to_show_on_mobile*365))]
        poll_avgs_track_plot_df = poll_avgs_track[poll_avgs_track.date >= (datetime.datetime.today() - datetime.timedelta(days=years_to_show_on_mobile*365))]
    else:
        poll_avgs_track_plot_df = poll_avgs_track

    plot = altair.Chart(joint_plot_df).mark_point(filled=True, size=100 if mobile_mode else 150)\
        .encode(x=altair.X('date:T', 
                    axis=altair.Axis(title='', tickCount = joint_plot_df.date.dt.year.nunique())),
            y=altair.Y('pct', axis=altair.Axis(title='Vote share / %')),
            color = altair.Color('party', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(polls_df.party)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(polls_df.party)]['colour'].tolist()
            )),
            shape = altair.Shape('pct_type', legend=altair.Legend(title='show...'),
                scale=altair.Scale(domain = ['polls','elections'], range = ['circle','diamond'])),
            size = altair.condition(altair.datum.pct_type == 'polls', altair.value(150), altair.value(300)),
            opacity = altair.condition(selection2, altair.value(0.5), altair.value(0.1)),
            tooltip = 'tooltip_text:N')\
        .add_selection(selection2)\
        .properties(title = '', 
            width = 600 if mobile_mode else 'container',  #rely on horizontal scroll on mobile
            height = 400)

    #Draw lines with separate data to get control on size
    plot2 = altair.Chart(poll_avgs_track_plot_df).mark_line(size=3)\
        .encode(x='date:T', y='pred_pct',
            color = altair.Color('party',
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(poll_avgs_track_plot_df.party)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(poll_avgs_track_plot_df.party)]['colour'].tolist()
                    ), 
                legend = altair.Legend(title = ''))
            )

    #plot = altair.layer(plot, plot2, selectors, points, text, rules, plot3)
    plot = altair.layer(plot2, plot)#, plot3)
    # plot = plot.configure_title(fontSize=20, font='Courier')
    plot = plot.configure_axis(titleFontSize=14, labelFontSize=10)
    plot = plot.configure_legend(
        direction='horizontal', 
        orient='top',
        strokeColor='gray',
        fillColor='#EEEEEE',
        padding=10,
        cornerRadius=10,
        columns = 4,
        #columns = 2 if mobile_mode else 4,  #if not using scroll, use this
        labelFontSize = 8 if mobile_mode else 12,
        symbolSize = 40 if mobile_mode else 60
    )

    return plot.to_json()

#Votes PCA plot of all MLAs 
@app.route('/data/plot_vote_pca_all_mlas_web')
def plot_vote_pca_all_mlas_fn_web():
    return plot_vote_pca_all_mlas_fn()

@app.route('/data/plot_vote_pca_all_mlas_mobile')
def plot_vote_pca_all_mlas_fn_mobile():
    return plot_vote_pca_all_mlas_fn(mobile_mode=True)

def plot_vote_pca_all_mlas_fn(mobile_mode = False):
    #mla_choice = 'Nichola Mallon'
    #print(my_pca.explained_variance_ratio_)

    plot = altair.Chart(mlas_2d_rep).mark_circle(size = 60 if mobile_mode else 120, opacity=0.6)\
        .encode(x=altair.X('x', 
                    axis=altair.Axis(title=['Principal component 1','(explains {:.0f}% variance)'.format(100*my_pca.explained_variance_ratio_[0])], labels=False)),
            y=altair.Y('y', axis=altair.Axis(title='Principal component 2 (explains {:.0f}% variance)'.format(100*my_pca.explained_variance_ratio_[1]), labels=False)),
            color = altair.Color('party', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(mlas_2d_rep.party)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(mlas_2d_rep.party)]['colour'].tolist()
                    ),
                 legend=altair.Legend(title="")),
            href = 'indiv_page_url:N',
            tooltip = 'normal_name:N')\
        .properties(title = '', 
            width = 'container', 
            height = 250 if mobile_mode else 500)
        #.configure_axisX(tickCount = 0)\
        #.configure_axisY(tickCount = 0)

    print('here',mobile_mode)
    plot = plot.configure_legend(
        direction='horizontal', 
        orient='top',
        strokeColor='gray',
        fillColor='#EEEEEE',
        padding=10,
        cornerRadius=10,
        columns=4,
        labelFontSize = 8 if mobile_mode else 12,
        symbolSize = 40 if mobile_mode else 60)
    #plot = plot.configure_legend(disable=True)

    #start fresh layer, otherwise fontSize is locked to size of points
    # text = altair.Chart(mlas_2d_rep[mlas_2d_rep.normal_name==mla_choice]).mark_text(
    #     align='left',
    #     baseline='middle',
    #     dx=12,
    #     fontSize=15
    # ).encode(
    #     text='normal_name', x='x', y='y'
    # )
    # plot += text

    return plot.to_json()

#How often party votes as one
@app.route('/data/plot_party_unity_bars')
def plot_party_unity_bars_fn():
    selection = altair.selection_single(on='mouseover', empty='all')
    plot = altair.Chart(votes_party_unity).mark_bar()\
        .add_selection(selection)\
        .encode(y='Percent voting as one',
            x=altair.Y('PartyName', sort='-y', axis = altair.Axis(title=None)),
            color = altair.Color('PartyName', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(votes_party_unity.PartyName)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(votes_party_unity.PartyName)]['colour'].tolist()
                    )),
            tooltip = 'tooltip_text')\
        .properties(title = ' ', width='container', height=300)
    plot = plot.configure_legend(disable=True)
 
    return plot.to_json()

#Top plenary topics
@app.route('/data/plot_plenary_topics_overall')
def plot_plenary_topics_overall_fn():
    topic_names = list(plenary_contribs_colour_dict.keys())
    selection = altair.selection_single(on='mouseover', empty='all')
    plot = altair.Chart(plenary_contribs_topic_counts).mark_bar(size=25)\
        .add_selection(selection)\
        .encode(
            x=altair.Y('n_contribs', axis=altair.Axis(title='Number of instances')),
            y=altair.Y('topic_name', sort='-x', axis = altair.Axis(title=None)),
            color=altair.Color('topic_name', 
                scale=altair.Scale(
                    domain=topic_names, 
                    range=[plenary_contribs_colour_dict[t] for t in topic_names]
                )
            ),
            #opacity = altair.condition(selection, altair.value(1), altair.value(0.3)),
            tooltip='tooltip_text:N')\
        .properties(title = ' ', width='container', height=450)\
        .configure_legend(disable=True)
    plot = plot.configure_axisY(labelFontSize=9)

    return plot.to_json()

#Plenary emotion scores
@app.route('/data/plot_plenary_emotions_by_party_web')
def plot_plenary_emotions_fn_web():
    return plot_plenary_emotions_fn()

@app.route('/data/plot_plenary_emotions_by_party_mobile')
def plot_plenary_emotions_fn_mobile():
    return plot_plenary_emotions_fn(mobile_mode=True)

def plot_plenary_emotions_fn(mobile_mode = False):
    selection_by_party = altair.selection_single(on='mouseover', empty='all', encodings=['color'])

    #TODO would be nice to have on hover, selected party vs average of the rest

    #fear and disgust are strongly correlated with anger and sadness, so can omit
    #surprise values are very similar for the 5 parties
    emotions_party_to_plot = emotions_party_agg[(emotions_party_agg.emotion_type.isin(
        ['anger','anticipation','joy','sadness','trust']
        )) & (emotions_party_agg.PartyName.isin(['Alliance','DUP','SDLP','Sinn Fein','UUP']))]

    plot = altair.Chart(emotions_party_to_plot).mark_point(size=100 if mobile_mode else 200, strokeWidth=3 if mobile_mode else 6)\
        .add_selection(selection_by_party)\
        .encode(
            x = altair.X('ave_emotion', axis=altair.Axis(title='Fraction words scoring')),
            y = altair.Y('emotion_type', axis=altair.Axis(title='')),
            color = altair.Color('PartyName', 
                scale=altair.Scale(
                    domain=party_colours[party_colours.party_name.isin(emotions_party_to_plot.PartyName)]['party_name'].tolist(), 
                    range=party_colours[party_colours.party_name.isin(emotions_party_to_plot.PartyName)]['colour'].tolist()
                ), legend=None),
            opacity = altair.condition(selection_by_party, altair.value(0.7), altair.value(0.1)),
            tooltip = 'PartyName'
        )\
        .properties(height = 180 if mobile_mode else 250, 
            width = 'container')

    return plot.to_json()

if __name__ == '__main__':
    if getpass.getuser() == 'david':
        app.run(debug=True)
    else:
        app.run(debug=False)  #don't need this for PythonAnywhere?